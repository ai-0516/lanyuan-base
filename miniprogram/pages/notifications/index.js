const { request } = require('../../utils/request');

Page({
  data: {
    notifications: [],  // 通知列表
  },

  onShow() {
    // 每次展示加载最新通知
    this.loadNotifications();
  },

  /** 加载未读通知列表 */
  async loadNotifications() {
    try {
      const res = await request('GET', '/notifications');
      // 格式化通知数据
      const list = (res.list || res.notifications || res || []).map(item => ({
        id: item.id,
        postId: item.post_id || item.postId,
        type: item.type,                          // like / comment / reply
        senderName: item.sender_name || item.senderName || '',
        senderAvatar: item.sender_avatar || item.senderAvatar || '',
        postExcerpt: item.post_excerpt || item.postExcerpt || '',
        read: !!item.read,
        time: this.formatTime(item.created_at || item.time || item.createdAt),
      }));
      this.setData({ notifications: list });
    } catch (err) {
      console.error('获取通知列表失败', err);
    }
  },

  /** 点击通知：标记已读并跳转 */
  async onTapNotification(e) {
    const { id, postId, post_id } = e.currentTarget.dataset;
    const notifId = id;
    const targetPostId = postId || post_id;

    // 标记已读
    try {
      await request('POST', '/notifications/read', { postId: targetPostId });
      // 更新本地状态
      const list = this.data.notifications.map(item => {
        if (item.id === notifId) {
          return { ...item, read: true };
        }
        return item;
      });
      this.setData({ notifications: list });
    } catch (err) {
      console.error('标记已读失败', err);
    }

    // 跳转到 feed 帖子详情
    if (targetPostId) {
      wx.navigateTo({
        url: `/pages/feed/detail/index?postId=${targetPostId}`,
      });
    }
  },

  /** 格式化时间 */
  formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;

    // 1 分钟内
    if (diff < 60000) return '刚刚';
    // 1 小时内
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    // 今天内
    if (date.toDateString() === now.toDateString()) {
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      return `${h}:${m}`;
    }
    // 昨天
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      return `昨天 ${h}:${m}`;
    }
    // 更早
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const m = String(date.getMinutes()).padStart(2, '0');
    return `${month}-${day} ${h}:${m}`;
  },
});
