/**
 * 网络请求工具函数
 * 封装 wx.request，统一处理 baseURL、错误码和登录态
 */

const { BASE_URL } = require('./constants')

/**
 * 发起 HTTP 请求
 *
 * 支持两种调用方式：
 *   request({ url, method, data, header })   — 新签名（推荐）
 *   request(method, url, data)               — 旧签名（向后兼容）
 *
 * @param {Object|string} options - 请求参数对象或 HTTP 方法
 * @param {string} [options.url] - 请求路径（相对路径自动拼接 BASE_URL）
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
    wx.request({
      url: url.startsWith('http') ? url : `${BASE_URL}${url}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...authHeader,
        ...header
      },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          const body = res.data;
          // 自动解包统一响应格式 { code: 0, data: ..., message: "ok" }
          resolve(body && body.code === 0 ? body.data : body);
        } else {
          reject(res);
        }
      },
      fail: (err) => {
        reject(err);
      }
    });
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

module.exports = { request, get, post, BASE_URL };
