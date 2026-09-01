/**
 * MySQL storage primitives：PersistenceBackend 物理层 8 hook（翻译自官方
 * SqliteStore，TECH_SPEC §5.2）。PersistenceCoordinator 编排层全复用，
 * 本 store 只写 MySQL：
 *
 * - appendBatch：InnoDB 事务 + `SELECT ... FOR UPDATE` 锁行读 tail →
 *   校验 `first.seq === next seq` → 批量 INSERT → revision+1（§5.2）
 * - loadStoredFrom：`WHERE seq >= ?`（§5.2：实现该 hook）
 * - TornMarker 用 number（MySQL 事务原子提交，torn 罕见，§5.2）
 * - 不做 chunk 打包 codec（行数非瓶颈，§5.2；逐事件 INSERT）
 * - storeIdentity = `mysql:{host}:{database}:store:{store_id}`（§8.2）
 * @module @lanyuan/dsh-session-persistence-mysql/store
 */

import { randomUUID } from 'node:crypto'
import mysql, { type Pool, type PoolConnection, type ResultSetHeader, type RowDataPacket } from 'mysql2/promise'
import {
  SessionPersistenceRevision,
  type PersistenceBackend,
  type SessionPersistenceRevision as PersistenceRevision,
  type SessionPersistenceSnapshot,
  type StoredPrefix,
  type StoredSuffix,
} from '@deepseek-ai/dsh-session-persistence'
import type { SessionEvent, SessionHeader, SessionId } from '@deepseek-ai/dsh-session'
import {
  type EventRow,
  type SessionRow,
  decodeEventRow,
  decodeSessionRow,
  rowToEvent,
  rowToMeta,
} from './schema.js'

/** mysql-persistence 插件连接配置（cordis.yml 的 config 来源）。 */
export interface MysqlStoreOptions {
  readonly host: string
  readonly port: number
  readonly user: string
  readonly password: string
  readonly database: string
/** 连接池大小（默认 5：workers=1 单进程，读 + 写并发足够）。 */
  readonly poolSize: number
}

interface SessionRowPacket extends RowDataPacket, SessionRow { }
interface EventRowPacket extends RowDataPacket, EventRow { }

/** 错误类型判定（mysql2 驱动错误）。 */
function isDuplicateKey(error: unknown): boolean {
  return typeof error === 'object' && error !== null
    && Reflect.get(error, 'code') === 'ER_DUP_ENTRY'
}

/** 版本化 revision：`{storeIdentity}:incarnation:{incarnation}:revision:{revision}`（§8.2）。 */
function mysqlRevision(storeIdentity: string, row: SessionRow): PersistenceRevision {
  return SessionPersistenceRevision(
    `${storeIdentity}:incarnation:${row.incarnation}:revision:${row.revision}`,
  )
}

/**
 * MySQL 实现 PersistenceBackend 物理层 hook。连接为懒建连接池（首次使用
 * 时 open），close() 释放。事务只用于写路径（appendBatch/commitRepair）；
 * 读路径单语句即一致快照（自动提交），无需显式事务。
 */
export class MysqlStore implements PersistenceBackend<number> {
  readonly name = 'session-persistence-mysql'

  private pool: Pool | undefined
  private storeIdentity: string | undefined
  private opened = false
  private ready: Promise<void> | undefined

  constructor(private readonly options: MysqlStoreOptions) { }

  /** 懒建连接池（表由 backend/alembic 管理，store 不建表——PR #97 review）。 */
  open(): Promise<void> {
    this.ready ??= this.openPool()
    return this.ready
  }

  private async openPool(): Promise<void> {
    const { host, port, user, password, database } = this.options
    this.pool = mysql.createPool({
      host,
      port,
      user,
      password,
      database,
      connectionLimit: this.options.poolSize ?? 5,
      // 连接保活：MySQL wait_timeout 默认 8h 关闭空闲连接（backend 同款
      // #64 教训）；取连接前探活 + 定期回收
      enableKeepAlive: true,
      keepAliveInitialDelay: 0,
      // 事务内查询等待锁的时长（appendBatch 的 FOR UPDATE 竞争窗口）
      waitForConnections: true,
      queueLimit: 0,
    })
    // persistence_state 表由 alembic 建；缺失 → 明确报错（fail-fast，部署需先
    // `alembic upgrade head`，见 TECH_SPEC §8.2「表结构真源」）
    this.storeIdentity = await this.resolveStoreIdentity()
    this.opened = true
  }

  /** 读/建 persistence_state 单例，产出 storeIdentity（§8.2）。 */
  private async resolveStoreIdentity(): Promise<string> {
    if (this.pool === undefined) throw new Error('mysql store pool is not open')
    const conn = await this.pool.getConnection()
    try {
      const [rows] = await conn.query<RowDataPacket[]>(
        'SELECT store_id FROM persistence_state WHERE singleton = 1',
      )
      let storeId: string
      if (rows.length === 0) {
        storeId = randomUUID()
        await conn.query(
          'INSERT INTO persistence_state (singleton, store_id) VALUES (1, ?)',
          [storeId],
        )
      } else {
        storeId = String(rows[0].store_id)
      }
      return `mysql:${this.options.host}:${this.options.database}:store:${storeId}`
    } finally {
      conn.release()
    }
  }

  private async connection(): Promise<PoolConnection> {
    await this.open()
    if (this.pool === undefined) throw new Error('mysql store pool is not open')
    return this.pool.getConnection()
  }

  // ── PersistenceBackend hooks（8 hook，TECH_SPEC §5.2）──

  /** 读存储前缀：sessions 行 + events 全量（coordinator 的 load 数据源）。 */
  async loadStored(id: SessionId, signal?: AbortSignal): Promise<StoredPrefix<number> | undefined> {
    signal?.throwIfAborted()
    const row = await this.rowFor(id)
    signal?.throwIfAborted()
    if (row === undefined) return undefined
    const eventRows = await this.eventRowsFor(id)
    signal?.throwIfAborted()
    const events = eventRows.map(rowToEvent)
    return {
      meta: rowToMeta(row),
      events,
      revision: mysqlRevision(this.storeIdentity as string, row),
    }
  }

  /** 读 stat 级 revision（不加载事件字节）。 */
  async readStoredRevision(id: SessionId, signal?: AbortSignal): Promise<PersistenceRevision | undefined> {
    signal?.throwIfAborted()
    const row = await this.rowFor(id)
    signal?.throwIfAborted()
    return row === undefined ? undefined : mysqlRevision(this.storeIdentity as string, row)
  }

  /** 读 `seq >= fromSeq` 的后缀（§5.2：实现 loadStoredFrom，coordinator 免全量解析）。 */
  async loadStoredFrom(id: SessionId, fromSeq: number, signal?: AbortSignal): Promise<StoredSuffix | undefined> {
    signal?.throwIfAborted()
    const row = await this.rowFor(id)
    signal?.throwIfAborted()
    if (row === undefined) return undefined
    const conn = await this.connection()
    try {
      const [rows] = await conn.query<EventRowPacket[]>(
        'SELECT seq, type, time, data, source_event_seqs, surface_op, ignorable\n'
        + 'FROM events WHERE session_id = ? AND seq >= ? ORDER BY seq ASC',
        [id, fromSeq],
      )
      signal?.throwIfAborted()
      return {
        meta: rowToMeta(row),
        events: rows.map(rowToEvent).filter(event => event.seq >= fromSeq),
      }
    } finally {
      conn.release()
    }
  }

  /**
   * 原子追加一批事件。InnoDB 事务：
   * 1. `SELECT ... FOR UPDATE` 锁 sessions 行（同 session 并发写串行化；
   *    workers=1 + coordinator per-session chain 双保险）
   * 2. 读 tail 校验 `first.seq === next seq`（续写契约）
   * 3. 未物化（首次 append）→ upsert sessions 行（header 物化）
   * 4. 批量 INSERT events
   * 5. revision + 1
   * @throws 续写 seq 不连续（stale append）或 sessions 行缺失（materialized 后）
   */
  async appendBatch(
    meta: SessionHeader,
    events: readonly SessionEvent[],
    isMaterialized: boolean,
  ): Promise<void> {
    await this.open()
    if (events.length === 0) return
    const conn = await this.connection()
    try {
      await conn.beginTransaction()
      // ① 锁行（当前读；行不存在则无锁——首次并发 INSERT 靠 PK 冲突兜底）
      const [rows] = await conn.query<SessionRowPacket[]>(
        'SELECT id, version, created_at, cwd, parent_session, seed_length, origin,\n'
        + '       delegation_depth, agent_preset, incarnation, revision, owner_user_id\n'
        + 'FROM sessions WHERE id = ? FOR UPDATE',
        [meta.id],
      )
      const existing = rows.length > 0 ? decodeSessionRow(rows[0] as unknown as Record<string, unknown>) : undefined

      // ② 校验续写起点
      const tail = await this.tailSeq(conn, meta.id)
      const expected = tail === undefined ? 0 : tail + 1
      const first = events[0] as SessionEvent
      if (first.seq !== expected) {
        throw new Error(`session ${meta.id} append starts at seq ${first.seq}, stored next seq is ${expected}`)
      }

      // ③ 未物化 → upsert header（owner_user_id/incarnation 不被覆盖：
      //    FastAPI 身份映射预写行（§6.3）与 DSH 首写在此收敛，互不覆盖）
      if (!isMaterialized) await this.upsertSession(conn, meta)

      // ④ 批量 INSERT events（逐事件，不做 chunk 打包 codec，§5.2）
      await this.insertEvents(conn, meta.id, events)

      // ⑤ revision + 1
      const [update] = await conn.query<ResultSetHeader>(
        'UPDATE sessions SET revision = revision + 1 WHERE id = ?',
        [meta.id],
      )
      if (update.affectedRows !== 1) {
        throw new Error(`session ${meta.id} metadata row is missing`)
      }
      await conn.commit()
    } catch (error: unknown) {
      await conn.rollback()
      if (isDuplicateKey(error)) {
        // 罕见：跨进程并发首 append 同 id（workers=1 下不应发生）→ 明确报错
        throw new Error(`session ${meta.id} append failed: duplicate session row`)
      }
      throw error
    } finally {
      conn.release()
    }
  }

  /**
   * 崩溃修复提交：tornMarker（number，MySQL 事务原子下罕见）→ 删除
   * `seq >= tornMarker` 的尾部 + 追加 closers；revision + 1。
   */
  async commitRepair(
    meta: SessionHeader,
    tornMarker: number | undefined,
    closers: readonly SessionEvent[],
  ): Promise<void> {
    await this.open()
    if (tornMarker === undefined && closers.length === 0) return
    const conn = await this.connection()
    try {
      await conn.beginTransaction()
      if (tornMarker !== undefined) {
        await conn.query('DELETE FROM events WHERE session_id = ? AND seq >= ?', [meta.id, tornMarker])
      }
      if (closers.length > 0) {
        const tail = await this.tailSeq(conn, meta.id)
        const expected = tail === undefined ? 0 : tail + 1
        if ((closers[0] as SessionEvent).seq !== expected) {
          throw new Error(`session ${meta.id} repair is stale: closer starts at seq ${(closers[0] as SessionEvent).seq}, stored next seq is ${expected}`)
        }
        await this.insertEvents(conn, meta.id, closers)
      }
      await conn.query('UPDATE sessions SET revision = revision + 1 WHERE id = ?', [meta.id])
      await conn.commit()
    } catch (error: unknown) {
      await conn.rollback()
      throw error
    } finally {
      conn.release()
    }
  }

  /** 列出全部已物化 session 的 header。 */
  async list(_signal?: AbortSignal): Promise<SessionHeader[]> {
    const rows = await this.sessionRows()
    return rows.map(rowToMeta)
  }

  /** 列出 header + revision（不加载事件字节）。 */
  async listSnapshots(_signal?: AbortSignal): Promise<SessionPersistenceSnapshot[]> {
    const rows = await this.sessionRows()
    return rows.map(row => ({
      header: rowToMeta(row),
      revision: mysqlRevision(this.storeIdentity as string, row),
    }))
  }

  /** 释放连接池（runtime shutdown 时）。 */
  async close(): Promise<void> {
    if (this.ready === undefined) return
    await Promise.allSettled([this.ready])
    if (!this.opened) return
    this.opened = false
    await this.pool?.end()
    this.pool = undefined
  }

  // ── 内部查询原语 ──

  private async rowFor(id: SessionId): Promise<SessionRow | undefined> {
    const conn = await this.connection()
    try {
      const [rows] = await conn.query<SessionRowPacket[]>(
        'SELECT id, version, created_at, cwd, parent_session, seed_length, origin,\n'
        + '       delegation_depth, agent_preset, incarnation, revision, owner_user_id\n'
        + 'FROM sessions WHERE id = ?',
        [id],
      )
      return rows.length === 0 ? undefined : decodeSessionRow(rows[0] as unknown as Record<string, unknown>)
    } finally {
      conn.release()
    }
  }

  private async eventRowsFor(id: SessionId): Promise<EventRow[]> {
    const conn = await this.connection()
    try {
      const [rows] = await conn.query<EventRowPacket[]>(
        'SELECT seq, type, time, data, source_event_seqs, surface_op, ignorable\n'
        + 'FROM events WHERE session_id = ? ORDER BY seq ASC',
        [id],
      )
      return rows.map(row => decodeEventRow(row as unknown as Record<string, unknown>))
    } finally {
      conn.release()
    }
  }

  private async sessionRows(): Promise<SessionRow[]> {
    const conn = await this.connection()
    try {
      const [rows] = await conn.query<SessionRowPacket[]>(
        'SELECT id, version, created_at, cwd, parent_session, seed_length, origin,\n'
        + '       delegation_depth, agent_preset, incarnation, revision, owner_user_id\n'
        + 'FROM sessions',
      )
      return rows.map(row => decodeSessionRow(row as unknown as Record<string, unknown>))
    } finally {
      conn.release()
    }
  }

  /** 锁内读 tail（appendBatch 事务内调用；锁持有者可见最新 committed 行）。 */
  private async tailSeq(conn: PoolConnection, id: SessionId): Promise<number | undefined> {
    const [rows] = await conn.query<EventRowPacket[]>(
      'SELECT seq FROM events WHERE session_id = ? ORDER BY seq DESC LIMIT 1',
      [id],
    )
    return rows.length === 0 ? undefined : Number(rows[0].seq)
  }

  /** upsert sessions 行（header 物化；不覆盖 incarnation/revision/owner_user_id，对齐官方 sqlite upsert 语义）。 */
  private async upsertSession(conn: PoolConnection, meta: SessionHeader): Promise<void> {
    await conn.query(
      'INSERT INTO sessions\n'
      + '  (id, version, created_at, cwd, parent_session, seed_length, origin,\n'
      + '   delegation_depth, agent_preset, incarnation, revision)\n'
      + 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)\n'
      + 'ON DUPLICATE KEY UPDATE\n'
      + '  version = VALUES(version), created_at = VALUES(created_at), cwd = VALUES(cwd),\n'
      + '  parent_session = VALUES(parent_session), seed_length = VALUES(seed_length),\n'
      + '  origin = VALUES(origin), delegation_depth = VALUES(delegation_depth),\n'
      + '  agent_preset = VALUES(agent_preset)',
      [
        meta.id,
        meta.version,
        meta.createdAt,
        meta.cwd ?? null,
        meta.parentSession ?? null,
        meta.seedLength ?? null,
        meta.origin ?? null,
        meta.delegationDepth ?? null,
        meta.agentPreset ?? null,
        randomUUID(),
      ],
    )
  }

  /** 批量 INSERT events（JSON 列显式序列化，避免 mysql2 对 null/对象歧义）。 */
  private async insertEvents(conn: PoolConnection, id: SessionId, events: readonly SessionEvent[]): Promise<void> {
    const values: unknown[] = []
    const placeholders: string[] = []
    for (const event of events) {
      placeholders.push('(?, ?, ?, ?, ?, ?, ?, ?)')
      // surface 字段只存在于 surface 事件（user/message、assistant/message、
      // tool/result）——SessionEvent 是 union，需经扩展类型访问（官方同款断言）
      const surface = event as SessionEvent & {
        sourceEventSeqs?: number[]
        surfaceOp?: string
      }
      values.push(
        id,
        event.seq,
        event.type,
        event.time,
        JSON.stringify(event.data),
        surface.sourceEventSeqs === undefined ? null : JSON.stringify(surface.sourceEventSeqs),
        surface.surfaceOp === undefined ? null : surface.surfaceOp,
        event.ignorable === undefined ? null : event.ignorable ? 1 : 0,
      )
    }
    await conn.query(
      'INSERT INTO events\n'
      + '  (session_id, seq, type, time, data, source_event_seqs, surface_op, ignorable)\n'
      + `VALUES ${placeholders.join(', ')}`,
      values,
    )
  }
}
