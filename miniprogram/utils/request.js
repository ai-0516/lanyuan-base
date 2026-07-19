/**
 * 网络请求工具函数
 * 封装 wx.request，统一处理 baseURL、错误码和登录态
 */

const { BASE_URL } = require('./constants')

/**
 * 发起 HTTP 请求
 * @param {Object} options - 请求参数
 * @param {string} options.url - 请求路径（相对路径自动拼接 BASE_URL）
 * @param {string} [options.method='GET'] - 请求方法
 * @param {Object} [options.data] - 请求数据
 * @param {Object} [options.header] - 自定义请求头
 * @returns {Promise} 请求结果 Promise
 */
function request(options) {
  const { url, method = 'GET', data, header } = options;

  return new Promise((resolve, reject) => {
    wx.request({
      url: url.startsWith('http') ? url : `${BASE_URL}${url}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...header
      },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res);
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

module.exports = { request, BASE_URL };
