const { request } = require('../../utils/request');
const { APP_VERSION, fullUrl } = require('../../utils/constants');
const auth = require('../../utils/auth');

Page({
  data: {
    userInfo: {},
    unreadCount: 0,
    showBuilding: true,
    showRoom: false,
    showLogoutModal: false,
    appVersion: APP_VERSION,
  },

  onShow() {
    this.loadUserInfo();
    this.loadUnreadCount();
  },

  async loadUserInfo() {
    try {
      const res = await request('GET', '/user/me');
      if (res.avatar) res.avatar = fullUrl(res.avatar);
      this.setData({
        userInfo: res,
        showBuilding: res.show_building !== false,
        showRoom: res.show_room === true,
      });
    } catch (err) {
      console.error('获取用户信息失败', err);
    }
  },

  async loadUnreadCount() {
    try {
      const res = await request('GET', '/notifications/count');
      this.setData({ unreadCount: res.count || 0 });
    } catch (err) {
      console.error('获取未读数失败', err);
    }
  },

  goToNotifications() {
    wx.navigateTo({ url: '/pages/notifications/index' });
  },

  goToEditProfile() {
    wx.navigateTo({ url: '/pages/edit-profile/index' });
  },

  async onToggleBuilding(e) {
    const showBuilding = !this.data.showBuilding;
    this.setData({ showBuilding });
    try {
      await request('PUT', '/user/me', { show_building: showBuilding });
    } catch (err) {
      console.error('更新失败', err);
      this.setData({ showBuilding: !showBuilding });
    }
  },

  async onToggleRoom(e) {
    const showRoom = !this.data.showRoom;
    this.setData({ showRoom });
    try {
      await request('PUT', '/user/me', { show_room: showRoom });
    } catch (err) {
      console.error('更新失败', err);
      this.setData({ showRoom: !showRoom });
    }
  },

  onLogout() {
    this.setData({ showLogoutModal: true });
  },

  onTapAbout() {
    wx.navigateTo({ url: '/pages/about/index' });
  },

  onTapAgreement() {
    wx.navigateTo({ url: '/pages/agreement/index' });
  },

  onTapPrivacy() {
    wx.navigateTo({ url: '/pages/privacy/index' });
  },

  hideLogoutModal() {
    this.setData({ showLogoutModal: false });
  },

  confirmLogout() {
    auth.clearToken();
    this.setData({ showLogoutModal: false });
    wx.reLaunch({ url: '/pages/login/index' });
  },
});
