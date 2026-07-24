// 登录页
const { request } = require('../../utils/request');
const { isLoggedIn } = require('../../utils/auth');

Page({
  data: {
    logging: false,
    checked: false, // 是否已完成自动登录检查
    avatar: '',
    nickname: '',
  },

  onLoad() {
    // 已登录且 Token 有效 → 直接跳首页
    if (isLoggedIn()) {
      this._autoLogin();
    } else {
      this.setData({ checked: true });
    }
  },

  /** 自动跳过登录 */
  async _autoLogin() {
    try {
      await request('GET', '/auth/check');
      wx.reLaunch({ url: '/pages/feed/index' });
    } catch {
      // Token 过期，留在登录页
      wx.removeStorageSync('token');
      wx.removeStorageSync('userInfo');
      this.setData({ checked: true });
    }
  },

  /** 微信头像选择回调 */
  onChooseAvatar(e) {
    const { avatarUrl } = e.detail;
    if (!avatarUrl) return;
    // 读取为 base64 data URI，直接传给后端存储
    try {
      const fm = wx.getFileSystemManager();
      const ext = avatarUrl.match(/\.(\w+)$/)?.[1] || 'jpeg';
      const base64 = fm.readFileSync(avatarUrl, 'base64');
      this.setData({ avatar: `data:image/${ext};base64,${base64}` });
    } catch (err) {
      console.error('读取头像失败', err);
    }
  },

  /** 处理微信一键登录 */
  async handleWxLogin() {
    if (this.data.logging) return;
    const nickname = (this.data.nickname || '').trim();
    if (!nickname) {
      wx.showToast({ title: '请填写昵称', icon: 'none' });
      return;
    }

    this.setData({ logging: true });

    try {
      // 微信登录
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      const payload = { code: loginRes.code, nickname };
      if (this.data.avatar) payload.avatar = this.data.avatar;
      const result = await request({
        method: 'POST',
        url: '/auth/login',
        data: payload,
      });

      // 存储 token 和用户信息
      wx.setStorageSync('token', result.token);
      wx.setStorageSync('userInfo', result.user);

      // 跳转到发现页
      wx.reLaunch({ url: '/pages/feed/index' });
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
