const { request } = require('../../utils/request');

Page({
  data: {
    form: {
      avatar: '',
      nickname: '',
      community: '',
      building: '',
      unit: '',
      room: '',
      bio: '',
    },
    isSaving: false,
  },

  goBack() {
    wx.navigateBack();
  },

  onLoad() {
    // 加载当前用户信息到表单
    this.loadUserInfo();
  },

  /** 加载当前用户信息 */
  async loadUserInfo() {
    try {
      const res = await request('GET', '/user/me');
      this.setData({
        form: {
          avatar: res.avatar || '',
          nickname: res.nickname || '',
          community: res.community || '',
          building: res.building || '',
          unit: res.unit || '',
          room: res.room || '',
          bio: res.bio || '',
        },
      });
    } catch (err) {
      console.error('加载用户信息失败', err);
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  /** 表单字段变化 */
  onFieldChange(e) {
    const field = e.currentTarget.dataset.field;
    const value = e.detail.value;
    this.setData({
      [`form.${field}`]: value,
    });
  },

  /** 点击选择头像 */
  onChooseAvatar() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const tempFile = res.tempFiles[0];
        // 上传头像到服务器
        this.uploadAvatar(tempFile.tempFilePath);
      },
    });
  },

  /** 上传头像 */
  async uploadAvatar(filePath) {
    const app = getApp();
    const token = wx.getStorageSync('token') || '';

    wx.uploadFile({
      url: `${app.globalData.baseUrl || ''}/upload/avatar`,
      filePath: filePath,
      name: 'avatar',
      header: {
        'Authorization': `Bearer ${token}`,
      },
      success: (res) => {
        try {
          const data = JSON.parse(res.data);
          if (data.code === 0 && data.data) {
            this.setData({
              'form.avatar': data.data.url || data.data,
            });
          }
        } catch (err) {
          console.error('解析上传结果失败', err);
        }
      },
      fail: (err) => {
        console.error('上传头像失败', err);
        wx.showToast({ title: '头像上传失败', icon: 'none' });
      },
    });
  },

  /** 保存 */
  async onSave() {
    if (this.data.isSaving) return;

    const { form } = this.data;
    if (!form.nickname.trim()) {
      wx.showToast({ title: '昵称不能为空', icon: 'none' });
      return;
    }

    this.setData({ isSaving: true });

    try {
      await request('PUT', '/user/me', form);
      wx.showToast({ title: '保存成功', icon: 'success' });
      setTimeout(() => {
        wx.navigateBack();
      }, 1000);
    } catch (err) {
      console.error('保存失败', err);
      wx.showToast({ title: '保存失败，请重试', icon: 'none' });
      this.setData({ isSaving: false });
    }
  },
});
