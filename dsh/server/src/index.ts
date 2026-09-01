/**
 * lanyuan JSON-RPC 插件入口（TECH_SPEC §5.3，M3）：替换官方
 * @deepseek-ai/dsh-sdk-jsonrpc-server（cordis.yml 中 sdk-jsonrpc-server
 * 条目改指本插件）。Stdout 保留给协议帧（不得加载 stdout logger）。
 * shutdown → 释放完整 root runtime 并 exit 0；bin 负责 EOF/信号退出。
 * 保持命名导出（name/inject/Config/apply，无 default export）——
 * Loader unwrapExports 依赖该形态（官方 postmortem 0001 教训）。
 *
 * @module @lanyuan/dsh-sdk-jsonrpc-server
 */

import type { Context } from '@deepseek-ai/cordis'
import type { Readable, Writable } from 'node:stream'
import Schema from '@deepseek-ai/schemastery'
import { JsonRpcLineTransport } from '@deepseek-ai/dsh-sdk-protocol'
import { LanyuanJsonRpcServer } from './server.js'

export * from './server.js'

export const name = 'sdk-jsonrpc-server'
// 只要求 agent factory；initialize 用 ctx.get() 读可选 LLM seam
export const inject = ['agents']

/** JSON-RPC 部署配置 + 运行时测试 hooks。 */
export interface JsonRpcConfig {
  /** 将 max-token turn 终止报为成功 SDK 结果。 */
  maxTokensAsSuccess?: boolean
  /** 传输输入覆盖；生产用 `process.stdin`。 */
  input?: Readable
  /** 传输输出覆盖；生产用 `process.stdout`。 */
  output?: Writable
  /** 进程退出覆盖；生产用 `process.exit`。 */
  exit?: (code: number) => void
}

export const Config: Schema<JsonRpcConfig> = Schema.object({
  maxTokensAsSuccess: Schema.boolean().default(false),
})

/**
 * 在配置流上服务 SDK 请求。effect 释放时 shutdown SDK 创建的 agent 并关闭
 * 传输。`shutdown` 响应 flush 后释放 root runtime 并 exit 0。
 */
export function apply(ctx: Context, config: JsonRpcConfig): void {
  const resolvedConfig = config as JsonRpcConfig & { maxTokensAsSuccess: boolean }
  // 协议 shutdown 拥有完整 runtime 进程（含 persistence），先等 root 生命周期
  const rootFiber = ctx.root.fiber
  /* v8 ignore next -- production stdio wiring; tests always inject the runtime hooks */
  const input = config.input ?? process.stdin
  /* v8 ignore next -- production stdio wiring; tests always inject the runtime hooks */
  const output = config.output ?? process.stdout
  /* v8 ignore next -- production exit wiring; tests always inject the runtime hooks */
  const exit = config.exit ?? ((code: number): void => { process.exit(code) })

  const transport = new JsonRpcLineTransport(input, output)
  const server = new LanyuanJsonRpcServer(ctx, transport, {
    maxTokensAsSuccess: resolvedConfig.maxTokensAsSuccess,
  })

  // 共享一个 exit task：并发的 shutdown 请求不能重复释放 root 或退出进程
  let exitTask: Promise<void> | undefined
  const disposeAndExit = (): Promise<void> => {
    exitTask ??= (async () => {
      await Promise.allSettled([Promise.resolve().then(() => transport.flush())])
      await Promise.allSettled([Promise.resolve().then(() => rootFiber.dispose())])
      exit(0)
    })()
    return exitTask
  }

  transport.onRequest(async (method, params) => {
    // `initialize` 是 SDK 的就绪边界。本插件可能先于异步 sibling Loader
    // 条目激活（如 MCP client 的工具发现），不要在完整 tree settle 前
    // 广播 ready runtime。
    if (method === 'initialize') await ctx.get('loader')?.await()
    const result = await server.handleRequest(method, params)
    if (method === 'shutdown') {
      // handler 结果写出后再跑；task 然后 flush/dispose/exit
      setImmediate(() => { void disposeAndExit() })
    }
    return result
  })

  ctx.effect(() => {
    transport.start()
    return async () => {
      await server.shutdown()
      transport.close()
    }
  }, 'jsonrpc.serve')
}
