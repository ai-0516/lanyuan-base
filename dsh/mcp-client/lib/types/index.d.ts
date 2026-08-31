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
import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
export declare const name = "lanyuan-mcp-client";
export declare const inject: string[];
/** MCP server（挂载在 FastAPI）的连接配置（cordis-lanyuan.yml 的 lanyuan-mcp-client 条目）。 */
export interface Config {
    /** 工具命名空间：工具注册为 `mcp__<serverName>__<rawName>`（§6.1）。 */
    serverName: string;
    /** MCP server streamable-http 端点（如 http://127.0.0.1:8000/mcp/）。 */
    url: string;
    /** 单次工具调用的超时（毫秒）。 */
    toolCallTimeoutMs: number;
}
export declare const Config: z<Config>;
export declare function apply(ctx: Context, config: Config): Promise<void>;
