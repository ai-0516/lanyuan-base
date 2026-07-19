/**
 * 兰园小程序 — 登录 & Token 管理
 *
 * 封装 wx.Storage 读写 Token，提供登录/登出/状态判断能力
 */

const { BASE_URL, STORAGE_KEYS, PAGES } = require('./constants')
const http = require('./request')

/**
 * 判断用户是否已登录（本地是否有 Token）
 * @returns {boolean}
 */
function isLoggedIn() {
  try {
    return !!wx.getStorageSync(STORAGE_KEYS.TOKEN)
  } catch {
    return false
  }
}

/**
 * 获取本地 Token
 * @returns {string}
 */
function getToken() {
  try {
    return wx.getStorageSync(STORAGE_KEYS.TOKEN) || ''
  } catch {
    return ''
  }
}

/**
 * 保存 Token 到本地
 * @param {string} token - JWT token 字符串
 */
function setToken(token) {
  wx.setStorageSync(STORAGE_KEYS.TOKEN, token)
}

/**
 * 清除本地 Token 和用户信息
 */
function clearToken() {
  wx.removeStorageSync(STORAGE_KEYS.TOKEN)
  wx.removeStorageSync(STORAGE_KEYS.USER_INFO)
}

/**
 * 获取缓存的用户信息
 * @returns {object|null}
 */
function getUserInfo() {
  try {
    return wx.getStorageSync(STORAGE_KEYS.USER_INFO) || null
  } catch {
    return null
  }
}

/**
 * 缓存用户信息到本地
 * @param {object} userInfo
 */
function setUserInfo(userInfo) {
  wx.setStorageSync(STORAGE_KEYS.USER_INFO, userInfo)
}

/**
 * 使用账号密码登录（调用服务端 /auth/login）
 *
 * @param {'phone'|'wechat'} mode - 登录方式
 * @param {object}   params - 登录参数
 * @param {string}   params.phone    - 手机号（mode=phone 时必填）
 * @param {string}   params.password - 密码（mode=phone 时必填）
 * @param {string}   params.code     - 微信静默 code（mode=wechat 时必填）
 * @returns {Promise<{token: string, user: object}>}
 */
async function login(mode = 'phone', params = {}) {
  let endpoint, payload

  if (mode === 'phone') {
    endpoint = '/auth/login'
    payload = {
      phone: params.phone,
      password: params.password,
    }
  } else if (mode === 'wechat') {
    endpoint = '/auth/wechat-login'
    payload = {
      code: params.code,
    }
  } else {
    throw new Error('不支持的登录方式: ' + mode)
  }

  const res = await http.post(endpoint, payload, { noAuth: true })

  if (res.code === 0 && res.data) {
    // 保存 Token 和用户信息
    setToken(res.data.token)
    if (res.data.user) {
      setUserInfo(res.data.user)
    }
    return res.data
  }

  throw new Error(res.message || '登录失败')
}

/**
 * 登出 — 清除本地凭据并跳转登录页
 */
function logout() {
  clearToken()
  wx.reLaunch({ url: PAGES.LOGIN })
}

/**
 * 检查登录态，未登录时跳转登录页
 * @returns {boolean} 是否已登录
 */
function checkLogin() {
  if (!isLoggedIn()) {
    wx.reLaunch({ url: PAGES.LOGIN })
    return false
  }
  return true
}

module.exports = {
  isLoggedIn,
  getToken,
  setToken,
  clearToken,
  getUserInfo,
  setUserInfo,
  login,
  logout,
  checkLogin,
}
