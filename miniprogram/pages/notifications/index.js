const { request } = require('../../utils/request');

Page({
  data: {
    notifications: [],
  },

  onShow() {
    this.loadNotifications();
  },

  goBack() {
    wx.navigateBack();
  },

  async loadNotifications() {
    try {
      const res = await request('GET', '/notifications');
      const list = (Array.isArray(res) ? res : []).map(item => ({
        ...item,
        displayTime: this.formatTime(item.created_at),
      }));
      this.setData({ notifications: list });
    } catch (err) {
      console.error('获取通知列表失败', err);
    }
  },

  async onTapNotification(e) {
    const { id, postId } = e.currentTarget.dataset;
    const targetPostId = postId || e.currentTarget.dataset.post_id;

    try {
      await request('POST', '/notifications/read', { postId: targetPostId });
      const list = this.data.notifications.map(item => {
        if (item.id === id) return { ...item, is_read: true };
        return item;
      });
      this.setData({ notifications: list });
    } catch (err) {
      console.error('标记已读失败', err);
    }

    if (targetPostId) {
      wx.switchTab({ url: '/pages/feed/index' });
    }
  },

  formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (date.toDateString() === now.toDateString()) {
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      return `${h}:${m}`;
    }
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      return `昨天 ${h}:${m}`;
    }
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${month}-${day}`;
  },
});
