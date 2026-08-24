/**
 * lanyuan agent spine（自写，裁剪自官方 @deepseek-ai/dsh-agent-spine-demo，MIT）。
 * 裁剪：skill / goals / bash / workspace-context / tool-jobs（lanyuan 社区问答场景不需要）；
 * 保留核心组装：LLM runtime / session store / title / system-prompt / tools / agent / agent-loop /
 * invariants / llm-retry / jobs-local（官方无条件 mount 的核心）。
 * 零 examples 依赖（§7.4）：不依赖官方 examples 包，所有能力来自正式 core 包（peer 声明）。
 * @module @lanyuan/dsh-agent-spine
 */

import type { Context } from '@deepseek-ai/cordis'
import Timer from '@deepseek-ai/cordis-plugin-timer'
import z from '@deepseek-ai/schemastery'
import LlmRuntime from '@deepseek-ai/dsh-llm'
import SessionStore from '@deepseek-ai/dsh-session'
import SessionTitleService, { type Config as SessionTitleConfig } from '@deepseek-ai/dsh-session-title'
import SystemPrompt, { type Config as SystemPromptConfig } from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { type Config as ToolsConfig } from '@deepseek-ai/dsh-tools'
import AgentRegistry from '@deepseek-ai/dsh-agent'
import LocalJobRegistry, { type Config as JobsConfig } from '@deepseek-ai/dsh-jobs-local'
import InvariantRegistry, { type Config as InvariantConfig } from '@deepseek-ai/dsh-invariants'
import * as sessionInvariant from '@deepseek-ai/dsh-session/invariant'
import * as agentInvariant from '@deepseek-ai/dsh-agent/invariant'
import * as scopeInvariant from '@deepseek-ai/dsh-scope/invariant'
import * as agentLoopInvariant from '@deepseek-ai/dsh-agent-loop/invariant'
import AgentLoop, { type Config as AgentLoopConfig } from '@deepseek-ai/dsh-agent-loop'
import * as llmRetry from '@deepseek-ai/dsh-llm-retry'
import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'

export const name = 'agent-spine'

/** Overridable example policy used when a bundle consumer omits `sessionTitle`. */
const EXAMPLE_SESSION_TITLE_CONFIG: SessionTitleConfig = {
  fallbackMaxWords: 5,
  fallbackMaxBytes: 40,
  maxTitleBytes: 80,
}

/**
 * Bundle config: each field forwarded verbatim to the child that owns it.
 * 裁剪说明（相对官方）：去掉 skill / goals / toolBash / toolJobs / workspaceContext——
 * lanyuan 社区问答场景无 shell/文件/技能/goal 需求（TECH_SPEC §7.1b）。
 */
export interface Config {
  /** The agent-loop `agents` list (see dsh-agent-loop's `Config`). */
  agents?: AgentLoopConfig['agents']
  /** Agent-loop concurrency cap; `1` is serial. */
  maxParallelToolCalls?: AgentLoopConfig['maxParallelToolCalls']
  /** Whether the system prompt includes the fixed Harness identity (default true). */
  includeHarnessIdentity?: SystemPromptConfig['includeHarnessIdentity']
  /** Whether model history includes dynamic runtime-context snapshots (default true). */
  includeRuntimeContext?: SystemPromptConfig['includeRuntimeContext']
  /** The deployment persona (see dsh-system-prompt's `Config`). */
  persona?: SystemPromptConfig['persona']
  /** The explicit model-facing tool order (see dsh-system-prompt's `Config`). */
  toolOrder?: SystemPromptConfig['toolOrder']
  /** The tool registry's config — its presentation `mode` (see dsh-tools' `Config`). */
  tools?: ToolsConfig
  /** DeepSeek Harness home directory shared by shell context and local skill discovery. */
  dshHome?: string
  /** Deterministic fallback and accepted-title limits; omission uses the bundle's example policy. */
  sessionTitle?: SessionTitleConfig
  /** Process-local background-job admission config. */
  jobs?: JobsConfig
  /** Global enablement and package-name filters for invariant companions. */
  invariants?: InvariantConfig
}

/** The session-title config schema with the shared bundle's overridable example limits. */
export const SessionTitleConfigSchema: z<SessionTitleConfig> = SessionTitleService.Config
  .default(EXAMPLE_SESSION_TITLE_CONFIG)

export const Config = z.intersect([
  AgentLoop.Config,
  SystemPrompt.Config,
  z.object({
    tools: ToolRuntime.Config,
    dshHome: z.string(),
    sessionTitle: SessionTitleConfigSchema,
    jobs: LocalJobRegistry.Config,
    invariants: InvariantRegistry.Config,
  }) as unknown as z<Pick<Config, 'tools' | 'dshHome' | 'sessionTitle' | 'jobs' | 'invariants'>>,
]) as unknown as z<Config>

/**
 * Load the spine. Each `ctx.plugin(...)` mounts one child of the bundle fiber;
 * `agent-loop` receives the forwarded `agents` list and `system-prompt` the
 * forwarded `persona` and `toolOrder`.
 */
export function apply(ctx: Context, config: Config): void {
  const dshHome = resolveDshHome(config.dshHome)

  ctx.plugin(Timer)
  ctx.plugin(LlmRuntime)
  ctx.plugin(SessionStore)
  ctx.plugin(SessionTitleService, config.sessionTitle ?? EXAMPLE_SESSION_TITLE_CONFIG)
  // Owner schemas resolve defaults; forward toolOrder only when explicitly set.
  ctx.plugin(SystemPrompt, {
    includeHarnessIdentity: config.includeHarnessIdentity ?? true,
    includeRuntimeContext: config.includeRuntimeContext ?? true,
    persona: config.persona ?? '',
    ...config.toolOrder !== undefined ? { toolOrder: config.toolOrder } : {},
  })
  ctx.plugin(ToolRuntime, config.tools ?? {})
  ctx.plugin(AgentRegistry)
  ctx.plugin(llmRetry)
  ctx.plugin(LocalJobRegistry, config.jobs ?? {})
  ctx.plugin(InvariantRegistry, config.invariants ?? {})
  ctx.plugin(sessionInvariant)
  ctx.plugin(agentInvariant)
  ctx.plugin(scopeInvariant)
  ctx.plugin(agentLoopInvariant)
  ctx.plugin(AgentLoop, {
    agents: config.agents ?? [],
    ...config.maxParallelToolCalls !== undefined ? { maxParallelToolCalls: config.maxParallelToolCalls } : {},
  })
}
