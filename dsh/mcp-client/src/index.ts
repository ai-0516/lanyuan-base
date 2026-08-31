/**
 * lanyuan MCP client 插件（TECH_SPEC §6）：官方 @deepseek-ai/dsh-mcp-client 的自写重写
 * （streamable-http，M2 review 定案）+ 注册 ToolRuntime + callTool 注入 user_id
 * （§6.3 身份强制绑定，LLM 永不提供身份）。
 *
 * MCP server 挂载在 FastAPI /mcp 端点（backend/tools/mcp_server/main.py），
 * 能力独立于 DSH runtime（DSH 只是众多 MCP client 之一）；本插件经
 * StreamableHTTPClientTransport 连接，executor 从 `exec.agent.session.id`
 * （格式 `v2-{user_id}-{uuid}`，FastAPI 侧编码，§5.1 过渡期每请求新 session）
 * 解析 user_id，注入 callTool 的 `_meta.user_id`（MCP 协议 RequestParams._meta，
 * 与传输方式无关，HTTP 同样透传）。
 *
 * 安全边界（§6.3 + PR #94 review 修复）：工具签名不含身份参数（LLM 零可见）；
 * 注入值来自 session id 而非模型输入（LLM 无法伪造）；传输层加**内部共享密钥**
 * （X-Lanyuan-Internal-Token，env LANYUAN_MCP_TOKEN，由 FastAPI 侧 dsh_runtime
 * 注入 DSH 子进程 env）——MCP server 只放行持有密钥的 client（本桥），外部
 * client 无法直连 /mcp 伪造 `_meta.user_id` 冒充用户（review 实测越权修复）。
 * 密钥缺失 → 拒绝连接（fail-closed，不携带密钥的 client 本就不该被放行）。
 *
 * @module @lanyuan/dsh-mcp-client
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import { CallToolResultSchema, ListToolsResultSchema } from '@modelcontextprotocol/sdk/types.js'
import type { Context } from '@deepseek-ai/cordis'
import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
import z from '@deepseek-ai/schemastery'

export const name = 'lanyuan-mcp-client'
export const inject = ['tools']

/** MCP server（挂载在 FastAPI）的连接配置（cordis-lanyuan.yml 的 lanyuan-mcp-client 条目）。 */
export interface Config {
  /** 工具命名空间：工具注册为 `mcp__<serverName>__<rawName>`（§6.1）。 */
  serverName: string
  /** MCP server streamable-http 端点（如 http://127.0.0.1:8000/mcp/）。 */
  url: string
  /** 单次工具调用的超时（毫秒）。 */
  toolCallTimeoutMs: number
}

export const Config = z.object({
  serverName: z.string().required(),
  url: z.string().required(),
  toolCallTimeoutMs: z.number().default(60000),
}) as unknown as z<Config>

/** session_id 格式：`v2-{user_id}-{uuid4()}`（backend/app/api/v2/ai.py 编码，§5.1）。 */
const SESSION_ID_PATTERN = /^v2-(\d+)-/

function parseUserId(sessionId: string): number | null {
  const m = SESSION_ID_PATTERN.exec(sessionId)
  return m ? Number(m[1]) : null
}

/** MCP content 数组 → 纯文本（LLM 可读的最简投影）。 */
function extractText(content: unknown[]): string {
  return content
    .map((block) => (
      typeof block === 'object' && block !== null && 'text' in block ? String((block as { text: unknown }).text) : ''
    ))
    .join('')
}

/** 启动窗口内的连接重试（MCP server 与 FastAPI 同进程同生命周期：lifespan 预热
 * DSH runtime 时 FastAPI 尚未 listen，首次 connect 必然 ECONNREFUSED——有界重试
 * 等 MCP 端点就绪；超过窗口仍未就绪则抛错（装配失败，runtime 不会静默缺工具）。
 * authToken：MCP 内部认证密钥（PR #94 review 修复），所有请求（含 GET 初始化）
 * 带 X-Lanyuan-Internal-Token，server 端校验（tools/mcp_server/security.py）。 */
async function connectWithRetry(config: Config, authToken: string): Promise<Client> {
  const MAX_RETRIES = 30
  const RETRY_INTERVAL_MS = 1000
  let lastError: unknown
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const client = new Client({ name: 'lanyuan-mcp-client', version: '0.1.0' }, { capabilities: {} })
    const transport = new StreamableHTTPClientTransport(
      new URL(config.url),
      { requestInit: { headers: { 'X-Lanyuan-Internal-Token': authToken } } },
    )
    try {
      await client.connect(transport)
      return client
    } catch (error) {
      lastError = error
      await transport.close().catch(() => {})
      await new Promise((resolve) => setTimeout(resolve, RETRY_INTERVAL_MS))
    }
  }
  throw new Error(
    `lanyuan-mcp-client(${config.serverName}): MCP server 连接失败（${config.url}，重试 ${MAX_RETRIES} 次后放弃）`,
    { cause: lastError },
  )
}

export async function apply(ctx: Context, config: Config): Promise<void> {
  const label = `lanyuan-mcp-client(${config.serverName})`

  // MCP 内部认证密钥（PR #94 review 修复）：server 端只放行持有
  // X-Lanyuan-Internal-Token 的 client（tools/mcp_server/security.py）。
  // token 由 FastAPI 侧 dsh_runtime 注入 DSH 子进程 env（LANYUAN_MCP_TOKEN，
  // 未配置时进程内自动生成同值注入）；缺失 → 拒绝连接（fail-closed）
  const authToken = process.env.LANYUAN_MCP_TOKEN
  if (!authToken) {
    throw new Error(`${label}: 缺少 LANYUAN_MCP_TOKEN（MCP 内部认证密钥），拒绝连接`)
  }

  // ⚠️ ctx.effect 必须在 active 上下文注册（await 之前）——cordis 的 async apply
  // Promise 不是 startup work，await 后调用会抛 INACTIVE_EFFECT。disposer 闭包
  // 引用后续填充的 client/disposers。
  const disposers: Array<() => void> = []
  let client: Client
  ctx.effect(() => () => {
    for (const dispose of disposers) dispose()
    void client?.close()
  }, 'lanyuan-mcp-client.connection')

  client = await connectWithRetry(config, authToken)

  const { tools } = await client.listTools()
  for (const tool of tools) {
    const rawName = tool.name
    const publicName = `mcp__${config.serverName}__${rawName}`
    const definition: ToolDefinition = {
      name: publicName,
      description: tool.description ?? '',
      parameters: (tool.inputSchema ?? { type: 'object', properties: {} }) as Record<string, unknown>,
      output: {
        schema: {
          type: 'object',
          properties: { content: { type: 'array', items: {} } },
          required: ['content'],
          additionalProperties: false,
        },
        render: (_args, value) => {
          const content = (value as { content?: unknown[] })?.content ?? []
          return [{ type: 'text', text: extractText(content) }]
        },
      },
      execute: async (args, exec) => {
        const userId = parseUserId(exec.agent?.session.id ?? '')
        if (userId === null) {
          throw new Error(`${label}: 无法从 session id 解析 user_id（桥层身份绑定失败）`)
        }
        const argsObj = (
          typeof args === 'object' && args !== null ? args : {}
        ) as Record<string, unknown>
        const result = await client.request(
          {
            method: 'tools/call',
            params: { name: rawName, arguments: argsObj, _meta: { user_id: userId } },
          },
          CallToolResultSchema,
          { signal: exec.signal, timeout: config.toolCallTimeoutMs },
        )
        const content = result.content as unknown[]
        const text = extractText(content)
        // MCP isError → throw，让 ToolRuntime 产出 isError 结果给模型
        if (result.isError === true) throw new Error(text)
        return { content }
      },
    }
    disposers.push(ctx.tools.register(definition))
  }
}
