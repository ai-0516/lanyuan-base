// 登录页
const { request } = require('../../utils/request');
const { BASE_URL } = require('../../utils/constants');

Page({
  data: {
    logging: false,
    avatar: '',     // 本地临时路径
    nickname: '',
  },

  /** 微信头像选择回调 */
  onChooseAvatar(e) {
    const { avatarUrl } = e.detail;
    if (avatarUrl) {
      this.setData({ avatar: avatarUrl });
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
      // 1. 微信登录
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      const result = await request({
        method: 'POST',
        url: '/auth/login',
        data: { code: loginRes.code, nickname, avatar: '' },
      });

      wx.setStorageSync('token', result.token);
      wx.setStorageSync('userInfo', result.user);

      // 2. 登录后上传头像（如有选择）
      const avatarPath = this.data.avatar;
      if (avatarPath) {
        try {
          const avatarUrl = await this._uploadFile(avatarPath, result.token);
          if (avatarUrl) {
            await request('PUT', '/user/me', { avatar: avatarUrl });
            result.user.avatar = avatarUrl;
            wx.setStorageSync('userInfo', result.user);
          }
        } catch (err) {
          console.error('头像上传失败，不影响登录', err);
        }
      }

      // 3. 跳转到发现页
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

  /** 上传文件到服务器，返回资源 URL */
  _uploadFile(filePath, token) {
    return new Promise((resolve, reject) => {
      wx.uploadFile({
        url: `${BASE_URL}/upload/images`,
        filePath,
        name: 'files',
        header: { Authorization: `Bearer ${token}` },
        success: (res) => {
          try {
            const body = JSON.parse(res.data);
            if (body.code === 0 && body.data?.urls?.length) {
              resolve(body.data.urls[0]);
            } else {
              reject(new Error('上传返回格式异常'));
            }
          } catch (err) {
            reject(err);
          }
        },
        fail: reject,
      });
    });
  },
});
