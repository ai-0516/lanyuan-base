// 登录页
const { request } = require('../../utils/request');
const auth = require('../../utils/auth');
const privacy = require('../../utils/privacy');

const STORAGE_KEY = 'lastProfile';

Page({
  data: {
    logging: false,
    checked: false,
    avatar: '',
    nickname: '',
    needPrivacyAuthorization: false,
    privacyAccepted: false,
    privacyContractName: privacy.DEFAULT_CONTRACT_NAME,
  },

  async onLoad() {
    const setting = await privacy.getPrivacySetting();
    this.setData({
      needPrivacyAuthorization: setting.needAuthorization,
      privacyAccepted: !setting.needAuthorization,
      privacyContractName: setting.privacyContractName,
    });
    this._continueAfterPrivacy();
  },

  _continueAfterPrivacy() {
    // 已登录且 Token 有效 → 直接跳首页
    if (auth.isLoggedIn()) {
      this._autoLogin();
    } else {
      this.setData({ checked: true });
      this._restoreProfile();
    }
  },

  onOpenPrivacyContract() {
    privacy.openPrivacyContract();
  },

  onPrivacyAgreementChange(e) {
    this.setData({ privacyAccepted: e.detail.value.includes('accepted') });
  },

  onAgreePrivacyAuthorization() {
    this.setData({
      needPrivacyAuthorization: false,
      privacyAccepted: true,
    });
    this.handleWxLogin();
  },

  /** 从本地缓存恢复用户主动设置过的昵称和头像 */
  _restoreProfile() {
    const saved = this._loadProfile();
    if (saved) {
      this.setData({ avatar: saved.avatar || '', nickname: saved.nickname || '' });
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
      auth.clearToken();
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
    if (!nickname || !this.data.avatar) {
      wx.showToast({ title: '请先设置头像和昵称', icon: 'none' });
      return;
    }
    if (!this.data.privacyAccepted) {
      wx.showToast({ title: '请先阅读并同意用户协议', icon: 'none' });
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

      auth.setToken(result.token);
      auth.setUserInfo(result.user);

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
