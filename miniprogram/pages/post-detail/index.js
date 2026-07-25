// 帖子详情页 — 和 feed 页完全一致的交互方式
const { request } = require('../../utils/request');
const { fullUrl } = require('../../utils/constants');

Page({
  data: {
    loading: true,
    post: null,
    /** 当前打开的滑出面板（空=关闭） */
    actionOpenId: '',
    /** 评论弹窗是否打开 */
    commentSheetOpen: false,
    commentText: '',
    canSend: false,
    /** 回复目标 */
    replyToId: null,
    replyToName: '',
    currentUserId: null,
  },

  onLoad(options) {
    const postId = options.id;
    if (!postId) {
      wx.showToast({ title: '缺少帖子ID', icon: 'none' });
      return;
    }
    this.postId = parseInt(postId, 10);
    const userInfo = wx.getStorageSync('userInfo') || {};
    this.setData({ currentUserId: userInfo.id || null });
    this.loadPost();
  },

  async loadPost() {
    this.setData({ loading: true });
    try {
      const post = await request('GET', `/posts/${this.postId}`);
      post.displayTime = this._formatTime(post.created_at);
      post.displayAvatar = fullUrl(post.user.avatar);
      if (post.images && post.images.length > 0) {
        post.displayImages = post.images.map(img => fullUrl(img));
      }
      if (post.comments && post.comments.length > 0) {
        post.displayComments = post.comments.map(cm => ({
          ...cm,
          displayTime: this._formatTime(cm.created_at),
          displayAvatar: fullUrl(cm.user.avatar),
        }));
      }
      post.likersText = (post.likers || []).map(l => l.nickname).join('，') + ' 觉得很赞';
      this.setData({ post, loading: false });
    } catch (err) {
      console.error('加载帖子失败', err);
      wx.showToast({ title: '加载失败', icon: 'none' });
      this.setData({ loading: false });
    }
  },

  /** 图片预览 */
  previewImage(e) {
    const { current, urls } = e.currentTarget.dataset;
    wx.previewImage({
      current: fullUrl(current),
      urls: (urls || []).map(u => fullUrl(u)),
    });
  },

  /** ── 滑出面板 ── */

  noop() {},

  toggleActions(e) {
    const faId = e.currentTarget.dataset.faId;
    this.setData({
      actionOpenId: this.data.actionOpenId === faId ? '' : faId,
    });
  },

  closeActions() {
    this.setData({ actionOpenId: '' });
  },

  /** ── 点赞 ── */

  async toggleLike(e) {
    const post = this.data.post;
    if (!post) return;
    const liked = !post.liked;
    const newLikers = liked
      ? (post.likers || []).concat([{ id: this.data.currentUserId, nickname: '' }])
      : (post.likers || []).filter(l => l.id !== this.data.currentUserId);
    this.setData({
      'post.liked': liked,
      'post.likers': newLikers,
      'post.likersText': newLikers.map(l => l.nickname || '').filter(Boolean).join('，') + ' 觉得很赞',
    });
    try {
      const res = await request('POST', `/posts/${post.id}/like`);
      this.setData({
        'post.liked': res.liked,
      });
      // 重新加载获取最新的 likers
      this.loadPost();
    } catch (err) {
      this.loadPost();
    }
  },

  /** ── 评论弹窗 ── */

  openCommentSheet() {
    this.setData({ commentSheetOpen: true, actionOpenId: '' });
  },

  closeCommentSheet() {
    this.setData({
      commentSheetOpen: false,
      commentText: '',
      canSend: false,
      replyToId: null,
      replyToName: '',
    });
  },

  onTapComment(e) {
    const { cid, cuid, cname } = e.currentTarget.dataset;
    if (cuid === this.data.currentUserId) return;
    this.setData({
      replyToId: cid,
      replyToName: cname,
      commentSheetOpen: true,
      actionOpenId: '',
    });
  },

  onCommentInput(e) {
    const val = e.detail.value;
    this.setData({ commentText: val, canSend: val.trim().length > 0 });
  },

  async sendComment() {
    const text = this.data.commentText.trim();
    if (!text) return;
    const payload = { content: text };
    if (this.data.replyToId) {
      payload.parent_comment_id = this.data.replyToId;
    }
    try {
      await request('POST', `/posts/${this.postId}/comments`, payload);
      this.closeCommentSheet();
      this.loadPost();
    } catch (err) {
      console.error('评论失败', err);
      wx.showToast({ title: '评论失败', icon: 'none' });
    }
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
});
