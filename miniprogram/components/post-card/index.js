// post-card 组件 — 帖子卡片（对齐 feed 流式布局）
// 只负责渲染和冒泡事件，不管理业务状态
const { fullUrl } = require('../../utils/constants');

Component({
  data: {
    displayImageItems: [],
  },

  properties: {
    post: { type: Object, value: {} },
    currentUserId: { type: Number, value: 0 },
    actionOpenId: { type: String, value: '' },
  },

  observers: {
    post(val) {
      this._syncDisplayImageItems(val);
    },
  },

  methods: {
    noop() {},

    _syncDisplayImageItems(post) {
      this.setData({ displayImageItems: this._buildDisplayImageItems(post || {}) });
    },

    _buildDisplayImageItems(post) {
      const labels = { pending: '图片审核中', rejected: '审核未通过' };
      const displayImages = post.displayImages
        || (post.images || []).map(img => fullUrl(img));
      return displayImages.map((url, index) => {
        const status = (post.image_moderation_statuses || [])[index] || '';
        return { url, status, label: labels[status] || '' };
      });
    },

    /** 点击空白区域 → 通知父页面收起滑出面板 */
    onBlankTap() {
      this.triggerEvent('blanktap');
    },

    onLike() {
      this.triggerEvent('like', { postId: this.data.post.id });
    },

    onToggleActions(e) {
      this.triggerEvent('toggleactions', {
        faId: e.currentTarget.dataset.faId,
      });
    },

    onComment() {
      this.triggerEvent('comment', { postId: this.data.post.id });
    },

    onTapComment(e) {
      const { postid, cid, cuid, cname } = e.currentTarget.dataset;
      this.triggerEvent('tapcomment', { postId: postid, cid, cuid, cname });
    },

    previewImage(e) {
      const { current, urls } = e.currentTarget.dataset;
      this.triggerEvent('previewimage', {
        current: fullUrl(current),
        urls: (urls || []).map(u => fullUrl(u)),
      });
    },

    onDelete() {
      this.triggerEvent('delete', { postId: this.data.post.id });
    },

    _formatTime(timestamp) {
      if (!timestamp) return '';
      const utcStr = typeof timestamp === 'string' && !timestamp.endsWith('Z') && !timestamp.includes('+')
        ? timestamp + 'Z' : timestamp;
      const date = new Date(utcStr);
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
  },
});
