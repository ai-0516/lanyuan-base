/**
 * 网络请求工具函数
 * 封装 HTTP 请求，统一处理 baseURL、错误码和登录态
 *
 * 双调用模式（2026-09-04 路线2，按 envVersion 自动分流）：
 * - 本地开发（develop）      → wx.request + BASE_URL（http://localhost:8000）
 * - 线上（trial / release）  → wx.cloud.callContainer（微信云托管私有链路，
 *   平台自动注入 x-wx-openid 等用户身份 header，免公网/免登录态交换）
 */

const { BASE_URL, CLOUD_CONFIG } = require('./constants')

/**
 * 是否为线上模式（wx.cloud.callContainer）
 * 微信 envVersion：develop（开发者工具/开发版）/ trial（体验版）/ release（正式版）
 * @returns {boolean}
 */
function isCloudMode() {
  try {
    const { miniProgram } = wx.getAccountInfoSync()
    return miniProgram.envVersion === 'trial' || miniProgram.envVersion === 'release'
  } catch (e) {
    // 极老基础库无 getAccountInfoSync → 按本地模式处理
    return false
  }
}

/**
 * 将请求 URL 转为云托管容器内 path
 * - 相对路径（'/auth/login'）→ '/api/v1/auth/login'（v1 API 前缀，与 BASE_URL 对齐）
 * - 绝对 URL（'http://localhost:8000/api/v2/ai/session'）→ 取 pathname '/api/v2/ai/session'
 * @param {string} url
 * @returns {string}
 */
function toCloudPath(url) {
  if (url.startsWith('http')) {
    // 保留 query string（如 ?page=1 分页参数），仅去掉 hash
    const m = url.match(/^https?:\/\/[^/]+(\/[^#]*)/)
    return m ? m[1] : url
  }
  const prefix = '/api/v1'
  return url.startsWith('/') ? prefix + url : `${prefix}/${url}`
}

/**
 * 发起 HTTP 请求
 *
 * 支持两种调用方式：
 *   request({ url, method, data, header })   — 新签名（推荐）
 *   request(method, url, data)               — 旧签名（向后兼容）
 *
 * @param {Object|string} options - 请求参数对象或 HTTP 方法
 * @param {string} [options.url] - 请求路径（相对路径自动拼接 BASE_URL；绝对 URL 原样使用）
 * @param {string} [options.method='GET'] - 请求方法
 * @param {Object} [options.data] - 请求数据
 * @param {Object} [options.header] - 自定义请求头
 * @param {string} [url] - 旧签名：请求路径
 * @param {Object} [data] - 旧签名：请求数据
 * @returns {Promise} 请求结果 Promise
 */
function request(options, url, data) {
  // 兼容旧签名：request('GET', '/path', data)
  if (typeof options === 'string') {
    return _doRequest({ method: options, url, data });
  }
  return _doRequest(options);
}

function _doRequest({ url, method = 'GET', data, header }) {
  // 自动注入 Authorization token（非登录接口）
  const token = wx.getStorageSync('token') || '';
  const authHeader = token ? { 'Authorization': `Bearer ${token}` } : {};

  return new Promise((resolve, reject) => {
    // 统一响应处理：2xx → 自动解包 { code: 0, data }；其余 reject
    const handleSuccess = (res) => {
      if (res.statusCode >= 200 && res.statusCode < 300) {
        const body = res.data;
        resolve(body && body.code === 0 ? body.data : body);
      } else {
        reject(res);
      }
    };
    const handleFail = (err) => reject(err);

    const fullHeader = {
      'Content-Type': 'application/json',
      ...authHeader,
      ...header
    };

    if (isCloudMode()) {
      // 线上：微信云托管私有链路（免公网，平台注入 x-wx-openid）
      wx.cloud.callContainer({
        config: { env: CLOUD_CONFIG.ENV },
        path: toCloudPath(url),
        method,
        data,
        header: {
          ...fullHeader,
          'X-WX-SERVICE': CLOUD_CONFIG.SERVICE, // 云托管服务名（必填）
        },
        success: handleSuccess,
        fail: handleFail,
      });
    } else {
      // 本地开发：wx.request 直连
      wx.request({
        url: url.startsWith('http') ? url : `${BASE_URL}${url}`,
        method,
        data,
        header: fullHeader,
        success: handleSuccess,
        fail: handleFail,
      });
    }
  });
}

/**
 * GET 请求快捷方法
 * @param {string} url - 请求路径
 * @param {Object} [header] - 自定义请求头
 * @returns {Promise}
 */
function get(url, header) {
  return request({ url, method: 'GET', header });
}

/**
 * POST 请求快捷方法
 * @param {string} url - 请求路径
 * @param {Object} [data] - 请求数据
 * @param {Object} [header] - 自定义请求头
 * @returns {Promise}
 */
function post(url, data, header) {
  return request({ url, method: 'POST', data, header });
}

module.exports = { request, get, post, BASE_URL, isCloudMode };
