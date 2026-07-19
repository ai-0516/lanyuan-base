const { request } = require('../../utils/request');

Page({
  data: {
    userInfo: {},       // 用户信息对象
    unreadCount: 0,     // 未读消息数
    showBuilding: true,  // 显示楼号开关
    showRoom: true,      // 显示房号开关
  },

  onShow() {
    // 每次展示重新加载用户数据和未读数
    this.loadUserInfo();
    this.loadUnreadCount();
  },

  /** 获取用户信息 */
  async loadUserInfo() {
    try {
      const res = await request('GET', '/user/me');
      this.setData({
        userInfo: res,
        showBuilding: res.show_building !== false,
        showRoom: res.show_room !== false,
      });
    } catch (err) {
      console.error('获取用户信息失败', err);
    }
  },

  /** 获取未读通知数 */
  async loadUnreadCount() {
    try {
      const res = await request('GET', '/notifications/count');
      this.setData({ unreadCount: res.count || 0 });
    } catch (err) {
      console.error('获取未读数失败', err);
    }
  },

  /** 跳转到消息通知页 */
  goToNotifications() {
    wx.navigateTo({ url: '/pages/notifications/index' });
  },

  /** 跳转到编辑资料页 */
  goToEditProfile() {
    wx.navigateTo({ url: '/pages/edit-profile/index' });
  },

  /** 切换显示楼号 */
  async onToggleBuilding(e) {
    const showBuilding = e.detail.value;
    this.setData({ showBuilding });
    try {
      await request('PUT', '/user/me', { show_building: showBuilding });
    } catch (err) {
      console.error('更新隐私设置失败', err);
      this.setData({ showBuilding: !showBuilding }); // 回滚
    }
  },

  /** 切换显示房号 */
  async onToggleRoom(e) {
    const showRoom = e.detail.value;
    this.setData({ showRoom });
    try {
      await request('PUT', '/user/me', { show_room: showRoom });
    } catch (err) {
      console.error('更新隐私设置失败', err);
      this.setData({ showRoom: !showRoom }); // 回滚
    }
  },

  /** 退出登录 */
  onLogout() {
    wx.showModal({
      title: '提示',
      content: '确定要退出登录吗？',
      success: (res) => {
        if (res.confirm) {
          // 清除 token
          wx.removeStorageSync('token');
          // 重新启动到登录页
          wx.reLaunch({ url: '/pages/login/index' });
        }
      },
    });
  },
});
