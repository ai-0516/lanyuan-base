/**
 * MysqlStore 物理层单测（TECH_SPEC §5.2/§8.2；验收：appendBatch 事务/revision）。
 *
 * 跑法：node --test（Node 原生测试器，跑编译产物 lib/types）——vitest 与
 * mysql2（CJS 循环 require）不兼容（RangeError），原生 Node 直接 require
 * mysql2 正常（node -e 已验）。
 *
 * 建表（snxly review：不重复维护 DDL）：三表由 backend/alembic 统一管理
 * （migration c2f7a9d4e5b6）——测试前先对测试库跑 `alembic upgrade head`
 * （verify_v2_m3.py 的 run_alembic_upgrade 会做），本文件 before hook 只校验
 * 表存在（缺失 fail-fast 提示），然后 TRUNCATE 清空数据（lanyuan_test 测试
 * 专用库，不触碰 lanyuan 生产库）；连接参数可用 LANYUAN_TEST_MYSQL_* 覆盖。
 */

import { after, before, beforeEach, describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import mysql from 'mysql2/promise'
import { MysqlStore } from '../lib/types/store.js'
import { decodeSessionRow, rowToMeta } from '../lib/types/schema.js'

const HOST = process.env.LANYUAN_TEST_MYSQL_HOST ?? '127.0.0.1'
const PORT = Number(process.env.LANYUAN_TEST_MYSQL_PORT ?? 3306)
const USER = process.env.LANYUAN_TEST_MYSQL_USER ?? 'lanyuan_test'
// 测试库凭据不进 git（PR #97 dev-lead review）：密码必须 env 注入，缺失 fail-fast
const PASSWORD = process.env.LANYUAN_TEST_MYSQL_PASSWORD
if (!PASSWORD) {
  throw new Error('LANYUAN_TEST_MYSQL_PASSWORD 未设置（lanyuan_test 测试库密码，凭据不进 git，请 export）')
}
const DATABASE = process.env.LANYUAN_TEST_MYSQL_DATABASE ?? 'lanyuan_test'

function makeStore() {
  return new MysqlStore({ host: HOST, port: PORT, user: USER, password: PASSWORD, database: DATABASE, poolSize: 2 })
}

function makeMeta(id = randomUUID()) {
  return { version: 0, id, createdAt: Date.now(), cwd: '/tmp' }
}

function makeEvent(meta, seq, type = 'user/message') {
  return { type, seq, time: Date.now(), data: { content: `message-${seq}` } }
}

/** 清空三表（测试隔离；外键顺序：先关 FK 检查再 TRUNCATE）。 */
async function resetTables(store) {
  const conn = await store.connection()
  await conn.query('SET FOREIGN_KEY_CHECKS = 0')
  await conn.query('TRUNCATE TABLE events')
  await conn.query('TRUNCATE TABLE sessions')
  await conn.query('TRUNCATE TABLE persistence_state')
  await conn.query('SET FOREIGN_KEY_CHECKS = 1')
  conn.release()
}

/** 建表校验（snxly review：表结构单一真源 = backend/alembic migration
 * c2f7a9d4e5b6，本文件不再持有/执行 DDL——测试库先跑 alembic upgrade head，
 * 这里只确认三表存在，缺失即 fail-fast 提示，避免「表不存在」被误当代码回归）。
 * 用独立连接不走 store.connection()（须在 store.open() 之前执行）。 */
async function assertTablesExist() {
  const conn = await mysql.createConnection({ host: HOST, port: PORT, user: USER, password: PASSWORD, database: DATABASE })
  try {
    const [rows] = await conn.query(
      "SELECT table_name AS tname FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN ('sessions', 'events', 'persistence_state')",
    )
    const found = new Set(rows.map((r) => r.tname))
    const missing = ['sessions', 'events', 'persistence_state'].filter((t) => !found.has(t))
    if (missing.length > 0) {
      throw new Error(
        `测试库缺少 v2 会话三表（${missing.join(', ')}）——表由 backend/alembic 管理，` +
          '请先对测试库执行 alembic upgrade head（或跑 scripts/verify_v2_m3.sh，其内部会自动 upgrade）',
      )
    }
  } finally {
    await conn.end()
  }
}

let store

before(async () => {
  await assertTablesExist()
  store = makeStore()
  await store.open()
  await resetTables(store)
})

after(async () => {
  await store.close()
})

describe('appendBatch（事务 + revision）', () => {
  beforeEach(async () => {
    await resetTables(store)
  })

  it('首次 append（未物化）→ sessions 行 + events 落库 + revision=1', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0), makeEvent(meta, 1)], false)
    const loaded = await store.loadStored(meta.id)
    assert.ok(loaded)
    assert.equal(loaded.meta.id, meta.id)
    assert.equal(loaded.events.length, 2)
    assert.equal(loaded.events[0].seq, 0)
    assert.match(loaded.revision, /:revision:1$/)
    const row = await store.rowFor(meta.id)
    assert.equal(row.revision, 1)
    assert.ok(row.incarnation)
  })

  it('续写（seq 连续）→ 事件追加 + revision 递增', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0)], false)
    const before = await store.readStoredRevision(meta.id)
    await store.appendBatch(meta, [makeEvent(meta, 1), makeEvent(meta, 2)], true)
    const loaded = await store.loadStored(meta.id)
    assert.equal(loaded.events.length, 3)
    const after = await store.readStoredRevision(meta.id)
    assert.match(after, /:revision:2$/)
    assert.notEqual(before, after)
  })

  it('seq 不连续（stale append）→ 抛错且不落库（revision 不变）', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0)], false)
    const before = await store.readStoredRevision(meta.id)
    await assert.rejects(
      store.appendBatch(meta, [makeEvent(meta, 2)], true),
      /append starts at seq 2/,
    )
    const loaded = await store.loadStored(meta.id)
    assert.equal(loaded.events.length, 1)
    assert.equal(await store.readStoredRevision(meta.id), before)
  })

  it('surface 字段（sourceEventSeqs/surfaceOp）落库往返不丢', async () => {
    const meta = makeMeta()
    const surfaceEvent = {
      type: 'assistant/message',
      seq: 0,
      time: Date.now(),
      data: { content: [{ type: 'text', text: 'hi' }], usage: {} },
      sourceEventSeqs: [0, 1],
      surfaceOp: 'add',
    }
    await store.appendBatch(meta, [surfaceEvent], false)
    const loaded = await store.loadStored(meta.id)
    assert.deepEqual(loaded.events[0].sourceEventSeqs, [0, 1])
    assert.equal(loaded.events[0].surfaceOp, 'add')
    assert.equal(loaded.events[0].data.content.length, 1)
  })
})

describe('读 hook（loadStoredFrom / revision / list）', () => {
  beforeEach(async () => {
    await resetTables(store)
  })

  it('loadStoredFrom 只读 seq >= fromSeq 的后缀', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0), makeEvent(meta, 1), makeEvent(meta, 2)], false)
    const suffix = await store.loadStoredFrom(meta.id, 2)
    assert.deepEqual(suffix.events.map((e) => e.seq), [2])
  })

  it('readStoredRevision 格式 = {storeIdentity}:incarnation:{...}:revision:{...}', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0)], false)
    const revision = await store.readStoredRevision(meta.id)
    assert.match(revision, new RegExp(`^mysql:${HOST}:${DATABASE}:store:[0-9a-f-]{36}:incarnation:[0-9a-f-]{36}:revision:\\d+$`))
  })

  it('不存在的 id → loadStored/readStoredRevision 返回 undefined', async () => {
    assert.equal(await store.loadStored(randomUUID()), undefined)
    assert.equal(await store.readStoredRevision(randomUUID()), undefined)
  })

  it('list/listSnapshots 列出已物化 sessions（含 header 与 revision）', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0)], false)
    const headers = await store.list()
    assert.ok(headers.some((h) => h.id === meta.id))
    const snapshots = await store.listSnapshots()
    const mine = snapshots.find((s) => s.header.id === meta.id)
    assert.ok(mine)
    assert.match(mine.revision, /:revision:1$/)
  })
})

describe('commitRepair（torn 修复）', () => {
  beforeEach(async () => {
    await resetTables(store)
  })

  it('tornMarker 删除尾部 + closers 续写 + revision 递增', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0), makeEvent(meta, 1), makeEvent(meta, 2)], false)
    // 模拟 torn：seq >= 2 视为损坏尾部，closers 从 seq 2 重写
    await store.commitRepair(meta, 2, [makeEvent(meta, 2, 'turn/end')])
    const loaded = await store.loadStored(meta.id)
    assert.deepEqual(loaded.events.map((e) => e.seq), [0, 1, 2])
    assert.equal(loaded.events[2].type, 'turn/end')
    assert.match(await store.readStoredRevision(meta.id), /:revision:2$/)
  })

  it('closers seq 不连续 → 抛错', async () => {
    const meta = makeMeta()
    await store.appendBatch(meta, [makeEvent(meta, 0)], false)
    await assert.rejects(store.commitRepair(meta, undefined, [makeEvent(meta, 5)]), /stale/)
  })
})

describe('schema 编解码', () => {
  it('decodeSessionRow 映射所有列（含 owner_user_id）', () => {
    const row = decodeSessionRow({
      id: '11111111-2222-4333-8444-555555555555', version: 1, created_at: 123, cwd: '/tmp', parent_session: null,
      seed_length: null, origin: null, delegation_depth: null, agent_preset: null,
      incarnation: 'abc', revision: 3, owner_user_id: 42,
    })
    assert.equal(row.id, '11111111-2222-4333-8444-555555555555')
    assert.equal(row.revision, 3)
    assert.equal(row.ownerUserId, 42)
    const meta = rowToMeta(row)
    assert.equal(meta.id, '11111111-2222-4333-8444-555555555555')
    assert.equal(meta.createdAt, 123)
    assert.equal(meta.cwd, '/tmp')
  })
})
