/**
 * lanyuan JSON-RPC server（TECH_SPEC §5.3，M3）：替换官方
 * @deepseek-ai/dsh-sdk-jsonrpc-server，核心差异 = get-or-load-or-create：
 *
 * ```
 * prompt(id) → 内存有？用内存的（session 亲和，同 id 复用 live session）
 *            : 持久化有？load 成 live session（agents.resume，§5.3 log-seed 重放）
 *            : 都没有？新建（agents.create）
 * ```
 *
 * 翻译自官方 HarnessSdkJsonRpcServer（MIT），裁剪：subagent 通知
 * （lanyuan 场景不启用 subagent，spine 无 subagent 组装）；保留
 * initialize/prompt/shutdown/handleRequest + session/event、agent/status 通知。
 * 官方 rc.5 缺口（§5.3）：`getOrCreateSession` 只查内存——本文件补上
 * 持久化恢复分支（「发现框架空白」叙事；Python SDK 零改动，服务端策略）。
 *
 * @module @lanyuan/dsh-sdk-jsonrpc-server/server
 */

import type { Context } from '@deepseek-ai/cordis'
import { resolve } from 'node:path'
import type { AgentHandle } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { SessionPersistence } from '@deepseek-ai/dsh-session-persistence'
import { SessionId } from '@deepseek-ai/dsh-session'
import * as LlmDeepSeek from '@deepseek-ai/dsh-llm-deepseek'
import type {
  InitializeParams,
  InitializeResult,
  JsonRpcTransportPeer,
  SessionEventNotification,
  SessionPromptParams,
  SessionPromptResult,
} from '@deepseek-ai/dsh-sdk-protocol'

interface SessionRecord {
  handle: AgentHandle
}

/** Deployment-specific status mapping for SDK turn outcomes. */
export interface LanyuanJsonRpcServerOptions {
  /** Report max-token termination as an accepted result instead of an infrastructure error. */
  maxTokensAsSuccess?: boolean
}

/**
 * SDK server over one booted harness context and transport peer。构造即订阅
 * session/agent 生命周期事件至 shutdown；不支持重新 initialize。
 */
export class LanyuanJsonRpcServer {
  private cwd = process.cwd()
  private provider = 'deepseek-official'
  private model = 'deepseek-official'
  private maxTokens: number | undefined
  private llmFiber: { dispose(): Promise<void> } | undefined
  /** live session 注册表（session 亲和：同 id 复用，§5.3）。 */
  private readonly sessions = new Map<string, SessionRecord>()
  /** 并发 get-or-load-or-create 去重（同 id 只建一次）。 */
  private readonly sessionCreations = new Map<string, Promise<SessionRecord>>()
  private readonly disposers: (() => void)[] = []
  private shutdownTask: Promise<Record<string, never>> | undefined
  private shuttingDown = false

  constructor(
    private readonly ctx: Context,
    private readonly transport: JsonRpcTransportPeer,
    private readonly options: LanyuanJsonRpcServerOptions = {},
  ) {
    this.disposers.push(ctx.on('session/event', (session, event) => {
      const payload: SessionEventNotification = { sessionId: String(session.id), event }
      this.transport.notify('session.event', payload)
    }))
    this.disposers.push(ctx.on('agent/status', ({ agent, status }) => {
      this.transport.notify('session.status', { sessionId: String(agent.session.id), status })
    }))
  }

  /**
   * 配置 SDK 路由；仅当无 provider adapter 时挂载 DeepSeek fallback。
   * @param params - SDK 握手参数。
   * @returns server 身份。
   */
  async initialize(params: InitializeParams): Promise<InitializeResult> {
    if (params.maxTokens !== undefined
      && (!Number.isSafeInteger(params.maxTokens) || params.maxTokens <= 0)) {
      throw new TypeError('initialize maxTokens must be a positive safe integer')
    }
    this.cwd = resolve(params.cwd)
    this.provider = params.provider
    this.model = params.model
    this.maxTokens = params.maxTokens
    if (!this.hasAdapterFor(this.provider)) {
      if (this.provider !== 'deepseek-official') throw new Error(`no adapter registered for provider "${this.provider}"`)
      this.llmFiber = await this.ctx.plugin(LlmDeepSeek, {})
    }
    return { serverInfo: { name: 'lanyuan-harness-sdk-runtime', version: '0.1.0' } }
  }

  /**
   * 队列一个带身份的 prompt（get-or-load-or-create 后 followup）。
   * @param params - 目标 session 与用户内容。
   * @returns 持久化的 message 身份。
   */
  async prompt(params: SessionPromptParams): Promise<SessionPromptResult> {
    const rec = await this.getOrCreateSession(params.sessionId)
    if (this.ctx.agents.get(rec.handle.agent.id) !== rec.handle.agent) {
      throw new Error(`session agent was disposed outside the server: ${params.sessionId}`)
    }
    const message = createUserMessage({ content: params.contentBlocks, source: { kind: 'user' } })
    rec.handle.agent.followup(message)
    return { messageId: message.id }
  }

  /** 释放 server 持有的 agent / adapter / 订阅至静默。 */
  shutdown(): Promise<Record<string, never>> {
    this.shutdownTask ??= this.performShutdown()
    return this.shutdownTask
  }

  private async performShutdown(): Promise<Record<string, never>> {
    this.shuttingDown = true
    const pendingCreations = [...this.sessionCreations.values()]
    await Promise.allSettled(pendingCreations)
    this.sessionCreations.clear()
    const records = [...this.sessions.values()]
    this.sessions.clear()
    const failures: unknown[] = []
    while (this.disposers.length > 0) {
      try {
        this.disposers.pop()?.()
      } catch (error) {
        failures.push(error)
      }
    }
    const teardownResults = await Promise.allSettled([
      ...records.map(rec => Promise.resolve().then(() => rec.handle.dispose())),
      ...(this.llmFiber === undefined ? [] : [Promise.resolve().then(() => this.llmFiber?.dispose())]),
    ])
    this.llmFiber = undefined
    failures.push(...teardownResults
      .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
      .map(result => result.reason as unknown))
    if (failures.length === 1) throw failures[0]
    if (failures.length > 1) throw new AggregateError(failures, 'SDK server teardown failed')
    return {}
  }

  /**
   * 分发一个 JSON-RPC 请求到类型化 handler；未知 method 抛错（→ JSON-RPC
   * error 响应）。
   */
  async handleRequest(method: string, params: Record<string, unknown> | undefined): Promise<unknown> {
    switch (method) {
      case 'initialize':
        return this.initialize(params as unknown as InitializeParams)
      case 'session/prompt':
        return this.prompt(params as unknown as SessionPromptParams)
      case 'shutdown':
        return this.shutdown()
      default:
        throw new Error(`unknown DeepSeek Harness SDK runtime method: ${method}`)
    }
  }

  /**
   * get-or-load-or-create（§5.3 核心）：内存有 → 用内存的；持久化有 →
   * load 成 live session（agents.resume，log-seed 重放）；都没有 → 新建。
   * session id 即身份（无条件 load：不区分「新会话」与「历史会话」——
   * 持久化层以 id 为准，§5.3 三个边界之一）。
   */
  private async getOrCreateSession(sessionId: string): Promise<SessionRecord> {
    if (this.shuttingDown) throw new Error('SDK server is shutting down')
    const existing = this.sessions.get(sessionId)
    if (existing) return existing
    const pending = this.sessionCreations.get(sessionId)
    if (pending) return pending
    const creation = this.createOrResumeSession(sessionId)
    this.sessionCreations.set(sessionId, creation)
    void creation.then(
      () => { this.sessionCreations.delete(sessionId) },
      () => { this.sessionCreations.delete(sessionId) },
    )
    return creation
  }

  /** 持久化恢复优先；collision 守卫（§5.3）：load 失败且 id 存在 → 抛错，不静默新建。 */
  private async createOrResumeSession(sessionId: string): Promise<SessionRecord> {
    // cordis 类型 map 不含 sessionPersistence 的声明合并（官方包），显式断言
    const persistence = this.ctx.get('sessionPersistence') as SessionPersistence | undefined
    if (persistence !== undefined) {
      try {
        const handle = await this.ctx.agents.resume({
          resumeSessionId: SessionId(sessionId),
          agentOptions: {
            provider: this.provider,
            model: this.model,
            ...this.maxTokens === undefined ? {} : { maxTokens: this.maxTokens },
          },
        })
        const rec: SessionRecord = { handle }
        this.sessions.set(sessionId, rec)
        return rec
      } catch (error) {
        // 照官方 agent-loop restoreOrCreateConfigured：load 后查存在性——
        // 存在（真 session 但 load 失败，如损坏）→ 抛错；不存在 → 走新建
        const exists = (await persistence.list())
          .some(header => String(header.id) === sessionId)
        if (exists) throw error
      }
    }
    const handle = await this.ctx.agents.create({
      sessionId: SessionId(sessionId),
      meta: { cwd: this.cwd },
      agentOptions: {
        provider: this.provider,
        model: this.model,
        ...this.maxTokens === undefined ? {} : { maxTokens: this.maxTokens },
      },
    })
    const rec: SessionRecord = { handle }
    this.sessions.set(sessionId, rec)
    return rec
  }

  private hasAdapterFor(provider: string): boolean {
    return this.ctx.get('llm')?.listProviders().some(entry => entry.id === provider) ?? false
  }
}
