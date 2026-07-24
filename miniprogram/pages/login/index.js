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

  async onLoad() {
    // 尝试自动获取微信头像和昵称（部分微信版本支持）
    this._tryAutoProfile();
    // 已登录且 Token 有效 → 直接跳首页
    if (isLoggedIn()) {
      this._autoLogin();
    } else {
      this.setData({ checked: true });
    }
  },

  /** 尝试自动获取微信昵称和头像 */
  async _tryAutoProfile() {
    // 优先从本地缓存恢复
    const saved = this._loadProfile();
    if (saved) {
      this.setData({ avatar: saved.avatar || '', nickname: saved.nickname || '' });
      return;
    }
    // 首次使用，尝试从微信自动获取（可能弹出授权框）
    try {
      const res = await new Promise((resolve, reject) => {
        wx.getUserProfile({
          desc: '用于完善个人资料',
          lang: 'zh_CN',
          success: resolve,
          fail: reject,
        });
      });
      const info = res.userInfo || {};
      const nickName = info.nickName || '';
      const avatarUrl = info.avatarUrl || '';
      // 头像转 base64
      let avatar = '';
      if (avatarUrl) {
        try {
          const fm = wx.getFileSystemManager();
          const base64 = fm.readFileSync(avatarUrl, 'base64');
          avatar = `data:image/jpeg;base64,${base64}`;
        } catch {
          avatar = avatarUrl;
        }
      }
      this.setData({ nickname: nickName, avatar });
      this._saveProfile(avatar, nickName);
    } catch {
      // 自动获取失败（用户拒绝或微信版本不支持），用户手动选择
    }
  },

  _loadProfile() {
    try {
      return wx.getStorageSync(STORAGE_KEY) || null;
    } catch {
      return null;
    }
  },

  _saveProfile(avatar, nickname) {
    try {
      wx.setStorageSync(STORAGE_KEY, { avatar, nickname });
    } catch {
      // 存储满等异常忽略
    }
  },

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

  onNicknameInput(e) {
    const nickname = e.detail.value;
    this.setData({ nickname });
    this._saveProfile(this.data.avatar, nickname);
  },

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
