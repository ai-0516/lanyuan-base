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
 * 安全边界（§6.3）：工具签名不含身份参数（LLM 零可见）；注入值来自
 * session id 而非模型输入（LLM 无法伪造）；MCP server 端只信 `_meta`。
 *
 * @module @lanyuan/dsh-mcp-client
 */
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { CallToolResultSchema } from '@modelcontextprotocol/sdk/types.js';
import z from '@deepseek-ai/schemastery';
export const name = 'lanyuan-mcp-client';
export const inject = ['tools'];
export const Config = z.object({
    serverName: z.string().required(),
    url: z.string().required(),
    toolCallTimeoutMs: z.number().default(60000),
});
/** session_id 格式：`v2-{user_id}-{uuid4()}`（backend/app/api/v2/ai.py 编码，§5.1）。 */
const SESSION_ID_PATTERN = /^v2-(\d+)-/;
function parseUserId(sessionId) {
    const m = SESSION_ID_PATTERN.exec(sessionId);
    return m ? Number(m[1]) : null;
}
/** MCP content 数组 → 纯文本（LLM 可读的最简投影）。 */
function extractText(content) {
    return content
        .map((block) => (typeof block === 'object' && block !== null && 'text' in block ? String(block.text) : ''))
        .join('');
}
/** 启动窗口内的连接重试（MCP server 与 FastAPI 同进程同生命周期：lifespan 预热
 * DSH runtime 时 FastAPI 尚未 listen，首次 connect 必然 ECONNREFUSED——有界重试
 * 等 MCP 端点就绪；超过窗口仍未就绪则抛错（装配失败，runtime 不会静默缺工具）。 */
async function connectWithRetry(config) {
    const MAX_RETRIES = 30;
    const RETRY_INTERVAL_MS = 1000;
    let lastError;
    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const client = new Client({ name: 'lanyuan-mcp-client', version: '0.1.0' }, { capabilities: {} });
        const transport = new StreamableHTTPClientTransport(new URL(config.url));
        try {
            await client.connect(transport);
            return client;
        }
        catch (error) {
            lastError = error;
            await transport.close().catch(() => { });
            await new Promise((resolve) => setTimeout(resolve, RETRY_INTERVAL_MS));
        }
    }
    throw new Error(`lanyuan-mcp-client(${config.serverName}): MCP server 连接失败（${config.url}，重试 ${MAX_RETRIES} 次后放弃）`, { cause: lastError });
}
export async function apply(ctx, config) {
    const label = `lanyuan-mcp-client(${config.serverName})`;
    // ⚠️ ctx.effect 必须在 active 上下文注册（await 之前）——cordis 的 async apply
    // Promise 不是 startup work，await 后调用会抛 INACTIVE_EFFECT。disposer 闭包
    // 引用后续填充的 client/disposers。
    const disposers = [];
    let client;
    ctx.effect(() => () => {
        for (const dispose of disposers)
            dispose();
        void client?.close();
    }, 'lanyuan-mcp-client.connection');
    client = await connectWithRetry(config);
    const { tools } = await client.listTools();
    for (const tool of tools) {
        const rawName = tool.name;
        const publicName = `mcp__${config.serverName}__${rawName}`;
        const definition = {
            name: publicName,
            description: tool.description ?? '',
            parameters: (tool.inputSchema ?? { type: 'object', properties: {} }),
            output: {
                schema: {
                    type: 'object',
                    properties: { content: { type: 'array', items: {} } },
                    required: ['content'],
                    additionalProperties: false,
                },
                render: (_args, value) => {
                    const content = value?.content ?? [];
                    return [{ type: 'text', text: extractText(content) }];
                },
            },
            execute: async (args, exec) => {
                const userId = parseUserId(exec.agent?.session.id ?? '');
                if (userId === null) {
                    throw new Error(`${label}: 无法从 session id 解析 user_id（桥层身份绑定失败）`);
                }
                const argsObj = (typeof args === 'object' && args !== null ? args : {});
                const result = await client.request({
                    method: 'tools/call',
                    params: { name: rawName, arguments: argsObj, _meta: { user_id: userId } },
                }, CallToolResultSchema, { signal: exec.signal, timeout: config.toolCallTimeoutMs });
                const content = result.content;
                const text = extractText(content);
                // MCP isError → throw，让 ToolRuntime 产出 isError 结果给模型
                if (result.isError === true)
                    throw new Error(text);
                return { content };
            },
        };
        disposers.push(ctx.tools.register(definition));
    }
}
