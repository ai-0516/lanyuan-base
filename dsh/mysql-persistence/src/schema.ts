/**
 * MySQL 物理表结构（TECH_SPEC §8.2）与行编解码。
 * 三表：sessions（header 物化 + incarnation/revision + owner_user_id 身份映射）、
 * events（事件日志，PK(session_id, seq)，FK → sessions CASCADE）、
 * persistence_state（store 身份单例）。
 *
 * 翻译自官方 SqliteStore 的 schema（schema-17），MySQL 差异：
 * - data / source_event_seqs 用 JSON 列（mysql2 自动 JSON.stringify/parse）
 * - ignorable 用 TINYINT(1)
 * - 不做 SQLite 的 user_version/application_id 严格校验（MySQL 无等价物）
 *
 * 表结构真源 = backend/alembic migration（c2f7a9d4e5b6，PR #97 review 定案：
 * 生产建表统一走 alembic；本文件只做行编解码，不持有 DDL——snxly review：
 * 避免多处维护同一个表结构（测试建表由 alembic 负责，store.test.mjs 校验表存在）。
 * @module @lanyuan/dsh-session-persistence-mysql/schema
 */

import { SessionId, type SessionHeader } from '@deepseek-ai/dsh-session'

/** sessions 表一行（decode 后）。 */
export interface SessionRow {
  id: string
  version: number
  createdAt: number
  cwd: string | null
  parentSession: string | null
  seedLength: number | null
  origin: string | null
  delegationDepth: number | null
  agentPreset: string | null
  incarnation: string
  revision: number
  ownerUserId: number | null
}

/** events 表一行（decode 后）。 */
export interface EventRow {
  seq: number
  type: string
  time: number
  data: unknown
  sourceEventSeqs: unknown
  surfaceOp: string | null
  ignorable: number | null
}

/**
 * 建表 DDL 不在此处（snxly review：避免多处维护同一个表结构）。
 * 表结构单一真源 = backend/alembic migration c2f7a9d4e5b6（PR #97 review 定案：
 * store 不执行建表；mysql-persistence 单测在 alembic 建好的测试库上跑，
 * store.test.mjs 的 before hook 校验三表存在，缺失即 fail-fast 提示先跑 alembic）。
 */

/** mysql2 行（snake_case 列名）→ SessionRow。 */
export function decodeSessionRow(row: Record<string, unknown>): SessionRow {
  return {
    id: String(row.id),
    version: Number(row.version),
    createdAt: Number(row.created_at),
    cwd: row.cwd === null || row.cwd === undefined ? null : String(row.cwd),
    parentSession: row.parent_session === null || row.parent_session === undefined ? null : String(row.parent_session),
    seedLength: row.seed_length === null || row.seed_length === undefined ? null : Number(row.seed_length),
    origin: row.origin === null || row.origin === undefined ? null : String(row.origin),
    delegationDepth: row.delegation_depth === null || row.delegation_depth === undefined ? null : Number(row.delegation_depth),
    agentPreset: row.agent_preset === null || row.agent_preset === undefined ? null : String(row.agent_preset),
    incarnation: String(row.incarnation),
    revision: Number(row.revision),
    ownerUserId: row.owner_user_id === null || row.owner_user_id === undefined ? null : Number(row.owner_user_id),
  }
}

/** mysql2 行 → EventRow。 */
export function decodeEventRow(row: Record<string, unknown>): EventRow {
  return {
    seq: Number(row.seq),
    type: String(row.type),
    time: Number(row.time),
    // mysql2 对 JSON 列自动 parse；防御性再解一次（双 JSON.stringify 的退化场景）
    data: typeof row.data === 'string' ? JSON.parse(row.data) : row.data,
    sourceEventSeqs: typeof row.source_event_seqs === 'string' ? JSON.parse(row.source_event_seqs) : row.source_event_seqs,
    surfaceOp: row.surface_op === null || row.surface_op === undefined ? null : String(row.surface_op),
    ignorable: row.ignorable === null || row.ignorable === undefined ? null : Number(row.ignorable),
  }
}

/** SessionRow → SessionHeader（coordinator 的 meta；照官方 SqliteStore rowToMeta）。 */
export function rowToMeta(row: SessionRow): SessionHeader {
  return {
    version: row.version,
    id: SessionId(row.id),
    createdAt: row.createdAt,
    ...row.cwd === null ? {} : { cwd: row.cwd },
    ...row.parentSession === null ? {} : { parentSession: SessionId(row.parentSession) },
    ...row.seedLength === null ? {} : { seedLength: row.seedLength },
    ...row.origin === null ? {} : { origin: row.origin as SessionHeader['origin'] },
    ...row.delegationDepth === null ? {} : { delegationDepth: row.delegationDepth },
    ...row.agentPreset === null ? {} : { agentPreset: row.agentPreset },
  }
}

/** EventRow → SessionEvent（data 为解析后的对象；surface 字段按官方 decode 同款断言）。 */
export function rowToEvent(row: EventRow): import('@deepseek-ai/dsh-session').SessionEvent {
  const event: Record<string, unknown> = {
    seq: row.seq,
    type: row.type,
    time: row.time,
    data: row.data,
  }
  if (row.sourceEventSeqs !== null && row.sourceEventSeqs !== undefined) event.sourceEventSeqs = row.sourceEventSeqs
  if (row.surfaceOp !== null && row.surfaceOp !== undefined) event.surfaceOp = row.surfaceOp
  if (row.ignorable !== null && row.ignorable !== undefined) event.ignorable = row.ignorable === 1
  return event as import('@deepseek-ai/dsh-session').SessionEvent
}
