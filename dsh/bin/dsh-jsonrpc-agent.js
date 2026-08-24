#!/usr/bin/env node
/**
 * lanyuan runtime 入口（自写 bin，替代官方 examples 包 dsh-sdk-jsonrpc-demo）。
 * 逻辑照官方 bin.js（62 行）简化：读 DSH_CORDIS_CONFIG → boot() → 信号处理。
 * 依赖 @deepseek-ai/dsh-app-boot（正式包，0.1.1-rc.2）。
 */
import { existsSync } from 'node:fs'
import { boot, installFailLoud, loadEnv, resolveConfigPath } from '@deepseek-ai/dsh-app-boot'

const NAME = 'dsh-jsonrpc-agent'

installFailLoud(NAME)
loadEnv(NAME)

const fromEnv = process.env['DSH_CORDIS_CONFIG']
const fromArgv = process.argv[2]
const requested = fromEnv !== undefined && fromEnv !== '' ? fromEnv : fromArgv
const configPath = requested === undefined ? undefined : resolveConfigPath(requested, undefined)

if (configPath === undefined || !existsSync(configPath)) {
  process.stderr.write(
    `usage: ${NAME} <path/to/cordis.yml> (or set DSH_CORDIS_CONFIG=<path>, which wins); the config is required — there is no built-in fallback\n`,
  )
  process.exit(1)
}

const ctx = await boot(NAME, configPath)

let exiting = false
async function disposeAndExit(code) {
  if (exiting) return
  exiting = true
  try {
    await ctx.fiber.dispose()
  } finally {
    process.exit(code)
  }
}

process.stdin.on('end', () => {
  disposeAndExit(0)
})
process.on('SIGTERM', () => {
  disposeAndExit(0)
})
process.on('SIGINT', () => {
  disposeAndExit(130)
})
