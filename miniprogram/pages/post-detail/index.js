// 帖子详情页
const { request } = require('../../utils/request');
const { fullUrl } = require('../../utils/constants');

Page({
  data: {
    loading: true,
    post: null,
    /** 评论输入 */
    commentText: '',
    canSend: false,
    /** 被回复的评论 ID（空=直接评论） */
    replyToId: null,
    /** 被回复的用户昵称 */
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
      // 预处理显示字段
      post.displayTime = this._formatTime(post.created_at);
      post.displayAvatar = fullUrl(post.user.avatar);
      // 图片 URL 预处理
      if (post.images && post.images.length > 0) {
        post.displayImages = post.images.map(img => fullUrl(img));
      }
      // 评论预处理
      if (post.comments && post.comments.length > 0) {
        post.displayComments = post.comments.map(cm => ({
          ...cm,
          displayTime: this._formatTime(cm.created_at),
          displayAvatar: fullUrl(cm.user.avatar),
        }));
      }
      // 点赞名单文字
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

  /** 点赞切换 */
  async toggleLike() {
    const post = this.data.post;
    if (!post) return;
    // 乐观更新
    const liked = !post.liked;
    const newLikers = this._updateLikers(post.likers, liked);
    this.setData({
      'post.liked': liked,
      'post.likers': newLikers,
      'post.likersText': newLikers.map(l => l.nickname || '').filter(Boolean).join('，') + ' 觉得很赞',
    });
    try {
      const res = await request('POST', `/posts/${post.id}/like`);
      const updatedLikers = res.liked
        ? (post.likers || []).concat([{ id: this.data.currentUserId, nickname: '' }])
        : (post.likers || []).filter(l => l.id !== this.data.currentUserId);
      this.setData({
        'post.liked': res.liked,
        'post.likers': updatedLikers,
        'post.likersText': updatedLikers.map(l => l.nickname || '').filter(Boolean).join('，') + ' 觉得很赞',
      });
    } catch (err) {
      // 回滚
      this.setData({
        'post.liked': post.liked,
        'post.likers': post.likers,
        'post.likersText': post.likersText,
      });
    }
  },

  _updateLikers(likers, liked) {
    if (!likers) return liked ? [{ id: this.data.currentUserId }] : [];
    if (liked) {
      return [...likers, { id: this.data.currentUserId }];
    }
    return likers.filter(l => l.id !== this.data.currentUserId);
  },

  /** 点击评论 — 设置回复目标 */
  onTapComment(e) {
    const { cid, cuid, cname } = e.currentTarget.dataset;
    if (cuid === this.data.currentUserId) return;
    this.setData({ replyToId: cid, replyToName: cname });
  },

  /** 评论输入 */
  onCommentInput(e) {
    const val = e.detail.value;
    this.setData({ commentText: val, canSend: val.trim().length > 0 });
  },

  /** 发送评论 */
  async sendComment() {
    const text = this.data.commentText.trim();
    if (!text) return;
    const payload = { content: text };
    if (this.data.replyToId) {
      payload.parent_comment_id = this.data.replyToId;
    }
    try {
      await request('POST', `/posts/${this.postId}/comments`, payload);
      this.setData({ commentText: '', canSend: false, replyToId: null, replyToName: '' });
      this.loadPost();
    } catch (err) {
      console.error('评论失败', err);
      wx.showToast({ title: '评论失败', icon: 'none' });
    }
  },

  /** 取消回复 */
  cancelReply() {
    this.setData({ replyToId: null, replyToName: '' });
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
