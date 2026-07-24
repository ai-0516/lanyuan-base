// 登录页
const { request } = require('../../utils/request');
const { isLoggedIn } = require('../../utils/auth');

const STORAGE_KEY = 'lastProfile';

Page({
  data: {
    logging: false,
    checked: false,
    avatar: '',
    nickname: '',
  },

  onLoad() {
    // 恢复上次保存的头像和昵称
    const saved = this._loadProfile();
    if (saved) {
      this.setData({ avatar: saved.avatar || '', nickname: saved.nickname || '' });
    }
    // 已登录且 Token 有效 → 直接跳首页
    if (isLoggedIn()) {
      this._autoLogin();
    } else {
      this.setData({ checked: true });
    }
  },

  /** 从本地存储加载上次的头像和昵称 */
  _loadProfile() {
    try {
      return wx.getStorageSync(STORAGE_KEY) || null;
    } catch {
      return null;
    }
  },

  /** 保存头像和昵称到本地 */
  _saveProfile(avatar, nickname) {
    try {
      wx.setStorageSync(STORAGE_KEY, { avatar, nickname });
    } catch {
      // 存储满等异常忽略
    }
  },

  /** 自动跳过登录 */
  async _autoLogin() {
    try {
      await request('GET', '/auth/check');
      wx.reLaunch({ url: '/pages/feed/index' });
    } catch {
      wx.removeStorageSync('token');
      wx.removeStorageSync('userInfo');
      this.setData({ checked: true });
    }
  },

  /** 微信头像选择回调 */
  onChooseAvatar(e) {
    const { avatarUrl } = e.detail;
    if (!avatarUrl) return;
    try {
      const fm = wx.getFileSystemManager();
      const ext = avatarUrl.match(/\.(\w+)$/)?.[1] || 'jpeg';
      const base64 = fm.readFileSync(avatarUrl, 'base64');
      const dataUri = `data:image/${ext};base64,${base64}`;
      this.setData({ avatar: dataUri });
      this._saveProfile(dataUri, this.data.nickname);
    } catch (err) {
      console.error('读取头像失败', err);
    }
  },

  /** 昵称输入变化 */
  onNicknameInput(e) {
    const nickname = e.detail.value;
    this.setData({ nickname });
    this._saveProfile(this.data.avatar, nickname);
  },

  /** 处理微信登录 */
  async handleWxLogin() {
    if (this.data.logging) return;
    const nickname = (this.data.nickname || '').trim();
    if (!nickname) {
      wx.showToast({ title: '请填写昵称', icon: 'none' });
      return;
    }

    this.setData({ logging: true });

    try {
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

      // 登录成功，保存 profile 供下次自动填入
      this._saveProfile(this.data.avatar, nickname);

      wx.setStorageSync('token', result.token);
      wx.setStorageSync('userInfo', result.user);

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
