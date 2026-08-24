/**
 * lanyuan 工具桥插件（TECH_SPEC §6）：spawn 业务 MCP server + 注册 ToolRuntime
 * + callTool 时注入 user_id（§6.3 身份强制绑定，LLM 永不提供身份）。
 *
 * 为什么自写（替代官方 @deepseek-ai/dsh-mcp-client，M2 机制验证定案）：
 * 官方 mcp-client 的 callTool 请求只带 {name, arguments}（tools.ts:88），
 * 无 `_meta` 扩展——user_id 注入无落点。本插件用 MCP SDK（正式库
 * @modelcontextprotocol/sdk，非 examples）自实现桥，executor 从
 * `exec.agent.session.id`（格式 `v2-{user_id}-{uuid}`，FastAPI 侧编码，§5.1
 * 过渡期每请求新 session）解析 user_id，注入 callTool 的 `_meta.user_id`。
 *
 * 安全边界（§6.3）：工具签名不含身份参数（LLM 零可见）；注入值来自
 * session id 而非模型输入（LLM 无法伪造）；MCP server 端只信 `_meta`。
 *
 * @module @lanyuan/dsh-lanyuan-bridge
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { CallToolResultSchema, ListToolsResultSchema } from '@modelcontextprotocol/sdk/types.js'
import type { Context } from '@deepseek-ai/cordis'
import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
import z from '@deepseek-ai/schemastery'

export const name = 'lanyuan-bridge'
export const inject = ['tools']

/** 一个 stdio MCP server 实例的配置（cordis-lanyuan.yml 的 lanyuan-bridge 条目）。 */
export interface Config {
  /** 工具命名空间：工具注册为 `mcp__<serverName>__<rawName>`（§6.1）。 */
  serverName: string
  /** MCP server 启动命令（backend venv python）。 */
  command: string
  /** MCP server 脚本路径。 */
  args: string[]
  /** 附加环境变量（叠加在 DSH 进程环境之上）。 */
  env: Record<string, string>
  /** MCP server 进程 cwd（backend/，使 settings 能读到 .env）。 */
  cwd: string
  /** 单次工具调用的超时（毫秒）。 */
  toolCallTimeoutMs: number
}

export const Config = z.object({
  serverName: z.string().required(),
  command: z.string().required(),
  args: z.array(String).default([]),
  env: z.dict(String).default({}),
  cwd: z.string().default(''),
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

export async function apply(ctx: Context, config: Config): Promise<void> {
  const label = `lanyuan-bridge(${config.serverName})`
  const transport = new StdioClientTransport({
    command: config.command,
    args: config.args,
    env: { ...(process.env as Record<string, string>), ...config.env },
    cwd: config.cwd === '' ? undefined : config.cwd,
  })
  const client = new Client({ name: 'lanyuan-bridge', version: '0.1.0' }, { capabilities: {} })

  // ⚠️ ctx.effect 必须在 active 上下文注册（await 之前）——cordis 的 async apply
  // Promise 不是 startup work，await 后调用会抛 INACTIVE_EFFECT（官方 mcp-client
  // 同模式：effect 先注册，连接/注册在 await 后做）。disposer 闭包引用后续填充。
  const disposers: Array<() => void> = []
  ctx.effect(() => () => {
    for (const dispose of disposers) dispose()
    void client.close()
  }, 'lanyuan-bridge.connection')

  try {
    await client.connect(transport)
  } catch (error) {
    await transport.close()
    throw new Error(`${label}: MCP server 连接失败`, { cause: error })
  }

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

  ctx.effect(() => () => {
    for (const dispose of disposers) dispose()
    void client.close()
  })
}
