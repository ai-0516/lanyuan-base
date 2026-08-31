/**
 * MySQL session-persistence provider（TECH_SPEC §5.2/§8.2，M3）。
 * 两层架构：PersistenceCoordinator 编排层全复用（@deepseek-ai/dsh-session-persistence
 * 官方包），物理层 8 hook 只写 MySQL store（./store.ts）。
 *
 * 激活方式（cordis-lanyuan.yml）：jsonl persistence 条目 `disabled: true`，
 * 本插件替换之（同服务 key `sessionPersistence`，激活两个会冲突）。
 *
 * 环境变量管理（§5.4 2g 教训）：连接参数经 env 显式注入 DSH 子进程
 * （LANYUAN_MYSQL_HOST/PORT/USER/PASSWORD/DATABASE），cordis.yml 用
 * `!!js process.env.XXX` 引用，密码不进 git。
 * @module @lanyuan/dsh-session-persistence-mysql
 */

import { Context, Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import type {
  SessionEvent,
  SessionHeader,
  SessionId,
  SessionPreparation,
} from '@deepseek-ai/dsh-session'
import {
  DEFAULT_PREPARED_SESSION_CACHE_SIZE,
  DEFAULT_WRITE_BATCH_MAX_DELAY_MS,
  MAX_WRITE_BATCH_DELAY_MS,
  PersistenceCoordinator,
  SessionPersistence,
  type SessionInspection,
  type SessionLocation,
  type SessionPersistenceSnapshot,
} from '@deepseek-ai/dsh-session-persistence'
import { MysqlStore } from './store.js'

export { SCHEMA_DDL } from './schema.js'

/** 插件配置（host/port/user/password/database；密码经 cordis.yml env 引用）。 */
export interface Config {
  /** MySQL 主机（生产云托管为内网地址）。 */
  host: string
  /** MySQL 端口，默认 3306。 */
  port: number
  /** 连接用户（须有 lanyuan 库建表/读写权限）。 */
  user: string
  /** 连接密码（经 env 注入，不进 git）。 */
  password: string
  /** 数据库名（v2 会话三表所在库，§8.2）。 */
  database: string
  /** 连接池大小，默认 5（workers=1 单进程足够）。 */
  poolSize?: number
  /** 保留的 cold SessionPreparation 数（转发 coordinator）。 */
  preparedSessionCacheSize?: number
  /** 事件合并写入窗口（转发 coordinator）。 */
  writeBatchMaxDelayMs?: number
}

/**
 * MySQL `SessionPersistence` provider。物理层 = MysqlStore（8 hook），
 * 编排层 = PersistenceCoordinator（官方全复用）。readFrom 走
 * loadStoredFrom hook（`WHERE seq >= ?`，§5.2），不做全量解析。
 */
export class MysqlSessionPersistence extends SessionPersistence {
  override readonly supportsRawArtifacts = false
  override readonly name = 'session-persistence-mysql'

  static inject = ['sessions']

  static Config: z<Config> = z.object({
    host: z.string().required(),
    port: z.number().default(3306),
    user: z.string().required(),
    password: z.string().required(),
    database: z.string().required(),
    poolSize: z.number().step(1).min(1).default(5),
    preparedSessionCacheSize: z.number().step(1).min(1).default(DEFAULT_PREPARED_SESSION_CACHE_SIZE),
    writeBatchMaxDelayMs: z.number().step(1).min(1).max(MAX_WRITE_BATCH_DELAY_MS)
      .default(DEFAULT_WRITE_BATCH_MAX_DELAY_MS),
  })

  private readonly store: MysqlStore
  private readonly coordinator: PersistenceCoordinator<number>

  constructor(ctx: Context, public config: Config) {
    super(ctx)
    const preparedSessionCacheSize = config.preparedSessionCacheSize
      ?? DEFAULT_PREPARED_SESSION_CACHE_SIZE
    const writeBatchMaxDelayMs = config.writeBatchMaxDelayMs
      ?? DEFAULT_WRITE_BATCH_MAX_DELAY_MS
    this.store = new MysqlStore({
      host: config.host,
      port: config.port,
      user: config.user,
      password: config.password,
      database: config.database,
      poolSize: config.poolSize ?? 5,
    })
    this.coordinator = new PersistenceCoordinator(this.ctx, this.store, {
      preparedSessionCacheSize,
      writeBatchMaxDelayMs,
    })
  }

  protected async [Service.init](): Promise<void> {
    await this.store.open()
  }

  /** MySQL 一张库内多 session，无独立 per-session artifact。 */
  locate(_meta: SessionHeader): SessionLocation | undefined {
    return undefined
  }
  create(meta: SessionHeader): Promise<void> {
    return this.coordinator.create(meta)
  }

  append(id: SessionId, events: readonly SessionEvent[]): Promise<void> {
    return this.coordinator.append(id, events)
  }

  override prepare(id: SessionId, signal?: AbortSignal): Promise<SessionPreparation> {
    return this.coordinator.prepare(id, signal)
  }

  load(id: SessionId): Promise<SessionInspection> {
    return this.coordinator.load(id)
  }

  inspect(id: SessionId, signal?: AbortSignal): Promise<SessionInspection> {
    return this.coordinator.inspect(id, signal)
  }

  readFrom(
    id: SessionId,
    fromSeq: number,
    signal?: AbortSignal,
  ): Promise<{ meta: SessionHeader; events: SessionEvent[] }> {
    return this.coordinator.readFrom(id, fromSeq, signal)
  }

  list(signal?: AbortSignal): Promise<SessionHeader[]> {
    return this.store.list(signal)
  }

  listSnapshots(signal?: AbortSignal): Promise<SessionPersistenceSnapshot[]> {
    return this.store.listSnapshots(signal)
  }
}

export default MysqlSessionPersistence
