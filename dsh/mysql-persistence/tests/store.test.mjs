/**
 * MysqlStore 物理层单测（TECH_SPEC §5.2/§8.2；验收：appendBatch 事务/revision）。
 *
 * 跑法：node --test（Node 原生测试器，跑编译产物 lib/types）——vitest 与
 * mysql2（CJS 循环 require）不兼容（RangeError），原生 Node 直接 require
 * mysql2 正常（node -e 已验）。测试前清空三表（lanyuan_test 测试专用库，
 * 不触碰 lanyuan 生产库）；连接参数可用 LANYUAN_TEST_MYSQL_* 覆盖。
 */

import { after, before, beforeEach, describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import mysql from 'mysql2/promise'
import { MysqlStore } from '../lib/types/store.js'
import { SCHEMA_DDL, SCHEMA_EVENTS_DDL, SCHEMA_PERSISTENCE_STATE_DDL, decodeSessionRow, rowToMeta } from '../lib/types/schema.js'

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

function makeMeta(id = `v2-${randomUUID()}`) {
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

/** 建三表（PR #97 review：生产表由 backend/alembic 管理，store 不建表——
 * 测试库 lanyuan_test 由本 hook 显式建表，DDL 与 alembic migration 同源）。
 * ⚠️ 须在 store.open() 之前执行（open → resolveStoreIdentity 依赖
 * persistence_state 表存在），故用独立连接不走 store.connection()。 */
async function createTables() {
  const conn = await mysql.createConnection({ host: HOST, port: PORT, user: USER, password: PASSWORD, database: DATABASE })
  try {
    for (const ddl of [SCHEMA_DDL, SCHEMA_EVENTS_DDL, SCHEMA_PERSISTENCE_STATE_DDL]) {
      await conn.query(ddl)
    }
  } finally {
    await conn.end()
  }
}

let store

before(async () => {
  await createTables()
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
    assert.equal(await store.loadStored(`v2-${randomUUID()}`), undefined)
    assert.equal(await store.readStoredRevision(`v2-${randomUUID()}`), undefined)
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
      id: 'v2-x', version: 1, created_at: 123, cwd: '/tmp', parent_session: null,
      seed_length: null, origin: null, delegation_depth: null, agent_preset: null,
      incarnation: 'abc', revision: 3, owner_user_id: 42,
    })
    assert.equal(row.id, 'v2-x')
    assert.equal(row.revision, 3)
    assert.equal(row.ownerUserId, 42)
    const meta = rowToMeta(row)
    assert.equal(meta.id, 'v2-x')
    assert.equal(meta.createdAt, 123)
    assert.equal(meta.cwd, '/tmp')
  })
})
