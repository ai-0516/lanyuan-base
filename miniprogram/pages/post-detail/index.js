// 帖子详情页
const { request } = require('../../utils/request');
const { fullUrl } = require('../../utils/constants');

Page({
  data: {
    loading: true,
    post: null,
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
      this.setData({
        post,
        loading: false,
        displayTime: this.formatTime(post.created_at),
      });
    } catch (err) {
      console.error('加载帖子失败', err);
      wx.showToast({ title: '加载失败', icon: 'none' });
      this.setData({ loading: false });
    }
  },

  /** 点赞切换 */
  async toggleLike() {
    const post = this.data.post;
    if (!post) return;
    // 乐观更新
    const liked = !post.liked;
    const likeCount = liked ? (post.like_count || 0) + 1 : Math.max(0, (post.like_count || 0) - 1);
    this.setData({
      post: { ...post, liked, like_count: likeCount },
    });
    try {
      const res = await request('POST', `/posts/${post.id}/like`);
      // 用服务器返回的正式值修正
      this.setData({
        'post.liked': res.liked,
        'post.like_count': res.likeCount,
      });
    } catch (err) {
      // 回滚
      this.setData({
        'post.liked': post.liked,
        'post.like_count': post.like_count,
      });
    }
  },

  /** 点击评论 — 设置回复目标 */
  onTapComment(e) {
    const { cid, cuid, cname } = e.currentTarget.dataset;
    // 自己的评论不显示回复，改为可删除（后续实现）
    if (cuid === this.data.currentUserId) return;
    this.setData({
      replyToId: cid,
      replyToName: cname,
    });
    // 聚焦输入框
    this._focusInput();
  },

  _focusInput() {
    // 延迟聚焦，等渲染完成
    setTimeout(() => {
      // 通过 wx.createSelectorQuery 找到 textarea 并聚焦
      // 微信小程序 textarea auto-focus 需要从用户事件触发
    }, 100);
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
      // 重新加载帖子以刷新评论列表
      this.loadPost();
    } catch (err) {
      console.error('评论失败', err);
      wx.showToast({ title: '评论失败', icon: 'none' });
    }
  },

  /** 取消回复（点击输入框旁 X 或其它操作） */
  cancelReply() {
    this.setData({ replyToId: null, replyToName: '' });
  },

  formatTime(timestamp) {
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
