#!/usr/bin/env node
/**
 * 前端 wx 通道 mock 验证（PR #101 路线2：callContainer HTTP + connectContainer WS）
 *
 * 用法：node backend/scripts/review/wx_mock/review.js   （零三方依赖，纯 node）
 *
 * 为什么入库（PR #101 第 3 轮 review 建议）：此前多轮声称「node mock 全链路通过」
 * 但仓库内无可复现脚本 → 沉淀到 scripts/review/，reviewer 可自行运行复现 D 维度
 * 证据。mock 对象只 stub wx 全局（getAccountInfoSync / getStorageSync / request /
 * cloud.callContainer / cloud.connectContainer / connectSocket），页面逻辑全真实。
 *
 * 断言点（对齐 daily log「node mock 全链路」+ 第 3 轮 connectContainer 修正）：
 *   HTTP（miniprogram/utils/request.js）：
 *   1. develop → wx.request localhost + Authorization header；2xx code===0 解包
 *   2. trial/release → wx.cloud.callContainer（config.env=CLOUD_CONFIG.ENV、
 *      X-WX-SERVICE=CLOUD_CONFIG.SERVICE、绝对 URL → 容器 path 且保留 query）
 *   3. wx.cloud 缺失 / callContainer 非函数 → reject 可读错误（基础库 ≥2.19.1）
 *   WS（miniprogram/pages/ai-chat/index.js streamChat，事件协议 §10.1）：
 *   4. trial/release → wx.cloud.connectContainer 参数（config.env / service /
 *      path=/api/v2/ai/chat/ws）→ socketTask 全链路驱动：open 发首帧
 *      {token, session_id, message} → turn/start → user/message → step/start →
 *      assistant/chunk（追加）→ turn/end → isLoading=false、用户/AI 气泡渲染正确
 *   5. connectContainer 缺失 → handleStreamError 可读错误（基础库 ≥2.21.1）
 *   6. 后端 error 帧 → 文案展示（登录已过期）→ isLoading=false
 *   7. 非 JSON 帧忽略（不崩、后续帧正常处理）
 *   8. 无 turn/end 的 onClose → 兜底 handleStreamError（isLoading 不卡死）
 *   9. connectContainer Promise reject → handleStreamError 通用文案
 *  10. develop → wx.connectSocket url = ws://localhost:8000/api/v2/ai/chat/ws
 */
'use strict'

const path = require('path')

const ROOT = path.join(__dirname, '../../../../') // → 仓库根
const MP = path.join(ROOT, 'miniprogram')

let passCount = 0
let failCount = 0
function ok(cond, msg) {
  if (cond) {
    passCount += 1
    console.log('  ✅ ' + msg)
  } else {
    failCount += 1
    console.error('  ❌ FAIL: ' + msg)
  }
}

// ── wx mock 状态（每个场景前重置）──────────────────────────────
let envVersion = 'develop' // getAccountInfoSync 返回
let cloudImpl = undefined // wx.cloud 整体（undefined = 基础库无云能力）
let connectSocketImpl = undefined // wx.connectSocket（develop WS）
const sentHttp = [] // wx.request / callContainer 捕获
const wsTasks = [] // 本次场景创建的 socketTask

function setWx({ version = 'develop', cloud, connectSocket } = {}) {
  envVersion = version
  cloudImpl = cloud
  connectSocketImpl = connectSocket
  sentHttp.length = 0
  wsTasks.length = 0
}

// 每次赋值都给全新 wx 全局（避免跨场景状态泄漏）；cloud/connectSocket 用
// getter 指向场景状态——request.js/_doRequest 与 ai-chat streamChat 运行时读取
global.wx = {
  getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
  getStorageSync: (k) => (k === 'token' ? 'jwt-token' : ''),
  request: (opts) => {
    sentHttp.push({ via: 'wx.request', opts })
    // 2xx + {code:0} → 调用方 resolve(data)
    opts.success({ statusCode: 200, data: { code: 0, data: { ok: true, echoed: opts.url } } })
  },
  get cloud() {
    return cloudImpl
  },
  get connectSocket() {
    return connectSocketImpl
  },
}

/** 生成与 wx.connectSocket/connectContainer 返回同构的 socketTask */
function makeTask() {
  const handlers = {}
  const task = {
    onOpen: (fn) => { handlers.open = fn },
    onMessage: (fn) => { handlers.message = fn },
    onError: (fn) => { handlers.error = fn },
    onClose: (fn) => { handlers.close = fn },
    send: (o) => { task.sent.push(o.data) },
    close: () => {},
    sent: [],
    fire: {
      open: () => handlers.open && handlers.open({}),
      message: (data) => handlers.message && handlers.message({ data }),
      close: (code) => handlers.close && handlers.close({ code }),
      error: () => handlers.error && handlers.error({}),
    },
  }
  wsTasks.push(task)
  return task
}

// ── 加载被测模块（require 时 wx 仅为占位，调用时才真正读写）──────
global.getApp = () => ({ towxml: () => ({}) }) // app.towxml stub（nodes 断言不依赖）
let pageConfig = null
global.Page = (cfg) => { pageConfig = cfg }

const constants = require(path.join(MP, 'utils/constants.js'))
const { request, isCloudMode } = require(path.join(MP, 'utils/request.js'))
require(path.join(MP, 'pages/ai-chat/index.js')) // 顶层 getApp()/Page() 需先 stub

/** 页面实例：config 方法 + data 深拷贝 + setData/scrollToBottom 简化 */
function makePage() {
  const page = {
    ...pageConfig,
    data: JSON.parse(JSON.stringify(pageConfig.data)),
  }
  page.setData = (patch) => Object.assign(page.data, patch)
  page.scrollToBottom = () => {}
  return page
}

// ── HTTP 场景 ────────────────────────────────────────────────
async function scenarioHttpDevelop() {
  console.log('① HTTP develop → wx.request + localhost')
  setWx({ version: 'develop' })
  const res = await request('GET', '/posts?page=1')
  ok(sentHttp.length === 1, '走 wx.request')
  const r = sentHttp[0]
  ok(r.via === 'wx.request', 'via=wx.request')
  ok(r.opts.url === constants.BASE_URL + '/posts?page=1', 'BASE_URL 拼接')
  ok(r.opts.header && r.opts.header.Authorization === 'Bearer jwt-token', 'Authorization 注入')
  ok(res.ok === true && res.echoed === constants.BASE_URL + '/posts?page=1', '2xx code0 解包')
}

async function scenarioHttpCloud() {
  console.log('② HTTP trial/release → wx.cloud.callContainer（env/service/path+query）')
  let callArgs = null
  setWx({
    version: 'release',
    cloud: {
      callContainer: (opts) => {
        callArgs = opts
        sentHttp.push({ via: 'callContainer', opts })
        opts.success({ statusCode: 200, data: { code: 0, data: { ok: true } } })
      },
    },
  })
  // 绝对 URL（v2 分页带 query）→ 容器 path 且 query 完整保留
  const res = await request('GET', 'http://localhost:8000/api/v2/ai/session/abc/messages?page=2&limit=20')
  ok(sentHttp[0].via === 'callContainer', '走 callContainer')
  ok(callArgs.config.env === constants.CLOUD_CONFIG.ENV, `config.env=${constants.CLOUD_CONFIG.ENV}`)
  ok(callArgs.path === '/api/v2/ai/session/abc/messages?page=2&limit=20', '绝对 URL → path 且保留 query')
  ok(callArgs.header['X-WX-SERVICE'] === constants.CLOUD_CONFIG.SERVICE, 'X-WX-SERVICE=服务名')
  ok(callArgs.header.Authorization === 'Bearer jwt-token', 'Authorization 注入')
  ok(res.ok === true, '2xx code0 解包')
}

async function scenarioHttpCloudMissing() {
  console.log('③ wx.cloud 缺失 / callContainer 非函数 → 可读 reject')
  setWx({ version: 'release', cloud: undefined })
  let msg = null
  await request('GET', '/posts').catch((e) => { msg = e.message })
  ok(msg && msg.includes('2.19.1'), 'wx.cloud 缺失 → 可读 reject（≥2.19.1 提示）')

  setWx({ version: 'release', cloud: {} }) // 2.2.3~2.19.0 区间：有 cloud 无 callContainer
  msg = null
  await request('GET', '/posts').catch((e) => { msg = e.message })
  ok(msg && msg.includes('2.19.1'), 'callContainer 非函数 → 可读 reject')
}

// ── WS 场景 ──────────────────────────────────────────────────
/** 云端 WS 全链路：connect → 首帧 → 事件序列 → turn/end */
async function scenarioWsCloud() {
  console.log('④ trial/release WS → wx.cloud.connectContainer + 事件全链路')
  let connectArgs = null
  const task = makeTask()
  setWx({
    version: 'release',
    cloud: {
      connectContainer: async (opts) => {
        connectArgs = opts
        return { socketTask: task }
      },
    },
  })
  const page = makePage()
  await page.streamChat('sess-cloud-1', '你好')
  ok(!!connectArgs, '调用了 wx.cloud.connectContainer')
  ok(connectArgs.config.env === constants.CLOUD_CONFIG.ENV, 'config.env=云托管环境 ID')
  ok(connectArgs.service === constants.CLOUD_CONFIG.SERVICE, 'service=云托管服务名')
  ok(connectArgs.path === '/api/v2/ai/chat/ws', 'path=/api/v2/ai/chat/ws（不发公网域名）')

  task.fire.open()
  ok(task.sent.length === 1, 'onOpen 发首帧')
  const first = JSON.parse(task.sent[0])
  ok(first.token === 'jwt-token' && first.session_id === 'sess-cloud-1' && first.message === '你好',
    '首帧 {token, session_id, message}')

  // turn/start → user/message → step/start → assistant/chunk → turn/end
  task.fire.message(JSON.stringify({ type: 'turn/start', data: {} }))
  task.fire.message(JSON.stringify({ type: 'user/message', data: { content: [{ type: 'text', text: '你好' }] } }))
  task.fire.message(JSON.stringify({ type: 'step/start', data: {} }))
  task.fire.message(JSON.stringify({ type: 'assistant/chunk', data: { chunk: { type: 'text-delta', text: '你好，' } } }))
  task.fire.message(JSON.stringify({ type: 'assistant/chunk', data: { chunk: { type: 'text-delta', text: '我是兰园 AI' } } }))
  task.fire.message(JSON.stringify({ type: 'turn/end', data: { reason: { kind: 'done' } } }))

  const roles = page.data.messages.map((m) => m.role)
  ok(roles.join(',') === 'user,assistant', `气泡顺序 user→assistant（实际 ${roles.join(',')}）`)
  ok(page.data.messages[0].content === '你好', '用户气泡内容正确')
  ok(page.data.messages[1].content === '你好，我是兰园 AI', 'AI 气泡 chunk 追加正确')
  ok(page.data.isLoading === false, 'turn/end → isLoading=false')
}

async function scenarioWsCloudMissing() {
  console.log('⑤ connectContainer 缺失 → 可读提示')
  setWx({ version: 'release', cloud: {} }) // 有 wx.cloud 但无 connectContainer（2.19.1~2.21.0）
  const page = makePage()
  await page.streamChat('sess-x', 'hi')
  const last = page.data.messages[page.data.messages.length - 1]
  ok(last && last.content.includes('2.21.1'), 'handleStreamError 展示 ≥2.21.1 升级提示')
  ok(page.data.isLoading === false, 'isLoading 复位')
}

async function scenarioWsErrorFrame() {
  console.log('⑥ 后端 error 帧 → 文案展示')
  const task = makeTask()
  setWx({ version: 'release', cloud: { connectContainer: async () => ({ socketTask: task }) } })
  const page = makePage()
  await page.streamChat('sess-y', 'hi')
  task.fire.open()
  task.fire.message(JSON.stringify({ type: 'error', data: { message: '登录已过期，请重新登录' } }))
  const last = page.data.messages[page.data.messages.length - 1]
  ok(last && last.role === 'assistant' && last.content === '登录已过期，请重新登录', 'error 帧文案渲染')
  ok(page.data.isLoading === false, 'isLoading=false')
}

async function scenarioWsNonJsonIgnored() {
  console.log('⑦ 非 JSON 帧忽略（不崩、后续帧正常）')
  const task = makeTask()
  setWx({ version: 'release', cloud: { connectContainer: async () => ({ socketTask: task }) } })
  const page = makePage()
  await page.streamChat('sess-z', 'hi')
  task.fire.open()
  task.fire.message('not-json-garbage') // 应 console.warn 后忽略
  task.fire.message(JSON.stringify({ type: 'user/message', data: { content: [{ type: 'text', text: '正常帧' }] } }))
  task.fire.message(JSON.stringify({ type: 'turn/end', data: { reason: { kind: 'done' } } }))
  const userMsg = page.data.messages.find((m) => m.role === 'user')
  ok(userMsg && userMsg.content === '正常帧', '垃圾帧被忽略，后续事件正常渲染')
}

async function scenarioWsOnCloseFallback() {
  console.log('⑧ 无 turn/end 的 onClose → 兜底收尾（isLoading 不卡死）')
  const task = makeTask()
  setWx({ version: 'release', cloud: { connectContainer: async () => ({ socketTask: task }) } })
  const page = makePage()
  page.data.isLoading = true // 模拟 onSend 已置位、流被异常掐断
  await page.streamChat('sess-w', 'hi')
  task.fire.open()
  task.fire.close() // 异常断开：无 error 帧无 turn/end
  ok(page.data.isLoading === false, 'onClose 兜底 → isLoading=false')
  const last = page.data.messages[page.data.messages.length - 1]
  ok(last && last.content.includes('请重试'), '兜底文案「请重试」渲染')
}

async function scenarioWsConnectReject() {
  console.log('⑨ connectContainer Promise reject → 通用文案收尾')
  setWx({
    version: 'release',
    cloud: { connectContainer: async () => { throw new Error('network down') } },
  })
  const page = makePage()
  await page.streamChat('sess-v', 'hi')
  const last = page.data.messages[page.data.messages.length - 1]
  ok(last && last.content.includes('请重试'), 'reject → handleStreamError 通用文案')
  ok(page.data.isLoading === false, 'isLoading=false')
}

async function scenarioWsDevelop() {
  console.log('⑩ develop WS → wx.connectSocket ws://localhost')
  const task = makeTask()
  setWx({ version: 'develop', connectSocket: (opts) => {
    task.wsUrl = opts.url
    return task
  } })
  const page = makePage()
  await page.streamChat('sess-local', 'hi')
  ok(task.wsUrl === 'ws://localhost:8000/api/v2/ai/chat/ws', 'url=ws://localhost:8000/api/v2/ai/chat/ws')
  task.fire.open()
  const first = JSON.parse(task.sent[0])
  ok(first.token === 'jwt-token' && first.message === 'hi', '首帧内容正确')
}

async function main() {
  await scenarioHttpDevelop()
  await scenarioHttpCloud()
  await scenarioHttpCloudMissing()
  await scenarioWsCloud()
  await scenarioWsCloudMissing()
  await scenarioWsErrorFrame()
  await scenarioWsNonJsonIgnored()
  await scenarioWsOnCloseFallback()
  await scenarioWsConnectReject()
  await scenarioWsDevelop()
  console.log(`\nnode mock 结果：${passCount} 通过 / ${failCount} 失败`)
  process.exit(failCount > 0 ? 1 : 0)
}

main().catch((e) => {
  console.error('❌ 脚本异常:', e)
  process.exit(1)
})
