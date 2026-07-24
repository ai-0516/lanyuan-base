/**
 * 兰园小程序 — 全局常量
 *
 * 集中管理 API 地址、颜色值、存储键等
 * 修改 API 地址只需改此处 BASE_URL
 */

/** API 基础地址（开发时可切换为局域网 IP） */
const BASE_URL = 'http://localhost:8000/api/v1'

/** 服务器根地址（用于拼接静态资源完整 URL） */
const SERVER_HOST = 'http://localhost:8000'

/** 请求超时时间（毫秒） */
const REQUEST_TIMEOUT = 15000

/** 存储键名 */
const STORAGE_KEYS = {
  TOKEN: 'token',           // JWT token
  USER_INFO: 'user_info',   // 用户信息缓存
}

/** 颜色常量（与 variables.wxss 保持一致，供 JS 动态使用） */
const COLORS = {
  cream: '#faf7f2',
  warmWhite: '#fffaf5',
  terracotta: '#c4673c',
  terracottaDeep: '#9b3d1a',
  terracottaLight: '#e8a87c',
  ember: '#d4744b',
  clay: '#b8532e',
  sand: '#f2e8dc',
  sandDark: '#e8d5c0',
  bark: '#3d2b1f',
  olive: '#6b8e5a',
  oliveLight: '#8aab7a',
  gold: '#c8963e',
  goldLight: '#e8c97a',
}

/** Tab 页路径映射 */
const TAB_PAGES = {
  AI_CHAT: '/pages/ai-chat/index',
  FEED: '/pages/feed/index',
  PROFILE: '/pages/profile/index',
}

/** 导航页面路径 */
const PAGES = {
  CREATE_POST: '/pages/create-post/index',
  NOTIFICATIONS: '/pages/notifications/index',
  EDIT_PROFILE: '/pages/edit-profile/index',
  LOGIN: '/pages/login/index',
}

/** 应用版本号 — 集中管理，所有页面统一读取 */
const APP_VERSION = '0.0.1'

/** HTTP 状态码 */
const HTTP_STATUS = {
  SUCCESS: 200,
  CREATED: 201,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  SERVER_ERROR: 500,
}

/** 统一响应码 */
const RESP_CODE = {
  SUCCESS: 0,         // 业务成功
  AUTH_FAIL: 1001,    // 认证失败
  TOKEN_EXPIRED: 1002,// Token 过期
  PARAM_ERROR: 2001,  // 参数错误
  SERVER_ERROR: 5000, // 服务器错误
}

/**
 * 将相对路径转为完整 URL（用于 image src）
 * @param {string} path - 图片路径，如 /uploads/xxx.jpeg
 * @returns {string} 完整 URL，非相对路径原样返回
 */
function fullUrl(path) {
  if (!path || path.startsWith('http') || path.startsWith('wxfile')) return path || ''
  return SERVER_HOST + path
}

module.exports = {
  BASE_URL,
  SERVER_HOST,
  fullUrl,
  REQUEST_TIMEOUT,
  STORAGE_KEYS,
  COLORS,
  TAB_PAGES,
  PAGES,
  APP_VERSION,
  HTTP_STATUS,
  RESP_CODE,
}
