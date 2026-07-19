// 登录页
const { request } = require('../../utils/request');

Page({
  data: {
    /** 是否正在登录 */
    logging: false,
  },

  /** 处理微信一键登录 */
  async handleWxLogin() {
    if (this.data.logging) return;

    this.setData({ logging: true });

    try {
      // 开发环境直接使用模拟 code
      const code = 'mock_code';
      const result = await request({ method: 'POST', url: '/auth/login', data: { code } });

      // 存储 token 和用户信息
      wx.setStorageSync('token', result.token);
      wx.setStorageSync('userInfo', result.user);

      // 登录成功，跳转到发现页
      wx.reLaunch({
        url: '/pages/feed/index',
      });
    } catch (err) {
      console.error('登录失败:', err);
      wx.showToast({
        title: err.message || '登录失败，请重试',
        icon: 'none',
        duration: 2000,
      });
    } finally {
      this.setData({ logging: false });
    }
  },
});
