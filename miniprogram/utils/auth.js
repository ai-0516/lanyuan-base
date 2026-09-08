/**
 * 兰园小程序 — 登录 & Token 管理
 *
 * 封装 wx.Storage 读写 Token，提供登录/登出/状态判断能力
 */

const { STORAGE_KEYS, PAGES } = require('./constants')

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
  logout,
  checkLogin,
}
