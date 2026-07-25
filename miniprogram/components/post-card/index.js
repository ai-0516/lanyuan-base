// post-card 组件 — 帖子卡片（对齐 feed 流式布局）
// 只负责渲染和冒泡事件，不管理业务状态
const { fullUrl } = require('../../utils/constants');

Component({
  properties: {
    post: { type: Object, value: {} },
    currentUserId: { type: Number, value: 0 },
    actionOpenId: { type: String, value: '' },
  },

  observers: {
    post(val) {
      if (!val || val._processed) return;
      const p = val;
      // 已由父页面预处理的值就不覆盖
      if (!p.displayTime) p.displayTime = this._formatTime(p.created_at);
      if (!p.displayAvatar) {
        p.displayAvatar = p.user?.avatar || `https://i.pravatar.cc/80?img=${(p.user?.id || 1) % 70}`;
      }
      if (p.images?.length && !p.displayImages) {
        p.displayImages = p.images.map(img => fullUrl(img));
      }
      if (p.comments?.length && !p.displayComments) {
        p.displayComments = p.comments.map(cm => ({
          ...cm,
          displayTime: this._formatTime(cm.created_at),
          displayAvatar: fullUrl(cm.user?.avatar),
        }));
      }
      if (!p.likersText) {
        p.likersText = (p.likers || []).map(l => l.nickname).filter(Boolean).join('，');
      }
      p._processed = true;
      this.setData({ post: p });
    },
  },

  methods: {
    noop() {},

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
