// 帖子详情页 — 和 feed 页完全一致的交互（区别：只显示一个帖子）
const { request } = require('../../utils/request');
const { fullUrl } = require('../../utils/constants');

Page({
  data: {
    loading: true,
    post: null,
    /** 滑出面板（详情页只有一个帖子，但组件需要此 prop） */
    actionOpenId: '',
    /** 评论弹窗 */
    commentSheetOpen: false,
    commentText: '',
    canSend: false,
    replyToId: null,
    replyToName: '',
    currentUserId: 0,
  },

  onLoad(options) {
    const postId = options.id;
    if (!postId) {
      wx.showToast({ title: '缺少帖子ID', icon: 'none' });
      return;
    }
    this.postId = parseInt(postId, 10);
    const userInfo = wx.getStorageSync('userInfo') || {};
    this.setData({ currentUserId: userInfo.id || 0 });
    this.loadPost();
  },

  async loadPost() {
    this.setData({ loading: true });
    try {
      const post = await request('GET', `/posts/${this.postId}`);
      post.displayImages = (post.images || []).map(img => fullUrl(img));
      this.setData({ post, loading: false });
    } catch (err) {
      console.error('加载帖子失败', err);
      wx.showToast({ title: '加载失败', icon: 'none' });
      this.setData({ loading: false });
    }
  },

  /* ===== post-card 组件事件 ===== */

  onPostToggleActions(e) {
    const { faId } = e.detail;
    this.setData({
      actionOpenId: this.data.actionOpenId === faId ? '' : faId,
    });
  },

  onPostLike(e) {
    const { postId } = e.detail;
    this._toggleLike(postId);
  },

  onPostComment(e) {
    const { postId } = e.detail;
    this._openCommentSheet(postId);
  },

  onPostTapComment(e) {
    const { postId, cid, cuid, cname } = e.detail;
    if (cuid === this.data.currentUserId) {
      wx.showActionSheet({
        itemList: ['删除'],
        success: (res) => {
          if (res.tapIndex === 0) this.deleteComment(cid);
        },
      });
    } else {
      this._openCommentSheet(postId);
      this.setData({ replyToId: cid, replyToName: cname });
    }
  },

  onPostPreviewImage(e) {
    const { current, urls } = e.detail;
    if (urls && urls.length > 0) {
      wx.previewImage({ urls, current });
    }
  },

  onPostDelete(e) {
    const { postId } = e.detail;
    this._deletePost(postId);
  },

  /* ===== 业务逻辑 ===== */

  async _toggleLike(postId) {
    const post = this.data.post;
    if (!post || post.id !== postId) return;
    const liked = !post.liked;
    const userInfo = wx.getStorageSync('userInfo') || {};
    const currentNickname = userInfo.nickname || '';
    let newLikers = [...(post.likers || [])];
    if (liked) {
      if (currentNickname && !newLikers.find(l => l.nickname === currentNickname)) {
        newLikers.push({ id: userInfo.id || 0, nickname: currentNickname });
      }
    } else {
      newLikers = newLikers.filter(l => l.nickname !== currentNickname);
    }
    this.setData({
      'post.liked': liked,
      'post.likers': newLikers,
      'post.likersText': newLikers.map(l => l.nickname).filter(Boolean).join('，'),
    });
    try {
      const res = await request('POST', '/posts/' + postId + '/like');
      // 用服务端返回修正
      this.setData({ 'post.liked': res.liked });
    } catch (err) {
      this.loadPost();
    }
  },

  _openCommentSheet(postId) {
    this.setData({
      commentSheetOpen: true,
      commentText: '',
      canSend: false,
      replyToId: null,
      replyToName: '',
    });
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

  onCommentInput(e) {
    const val = e.detail.value;
    this.setData({ commentText: val, canSend: val.trim().length > 0 });
  },

  async sendComment() {
    const text = this.data.commentText.trim();
    if (!text || !this.data.canSend) return;
    try {
      const payload = { content: text };
      if (this.data.replyToId) payload.parent_comment_id = this.data.replyToId;
      await request('POST', '/posts/' + this.postId + '/comments', payload);
      this.closeCommentSheet();
      this.loadPost();
    } catch (err) {
      console.error('评论失败', err);
      wx.showToast({ title: '评论失败', icon: 'none' });
    }
  },

  async _deletePost(postId) {
    try {
      await new Promise((resolve, reject) => {
        wx.showModal({
          title: '确认删除',
          content: '删除后无法恢复',
          success: (res) => res.confirm ? resolve() : reject('cancel'),
          fail: reject,
        });
      });
    } catch {
      return;
    }
    try {
      await request('DELETE', '/posts/' + postId);
      wx.showToast({ title: '已删除', icon: 'success' });
      wx.navigateBack();
    } catch (err) {
      console.error('删除失败', err);
      wx.showToast({ title: '删除失败', icon: 'error' });
    }
  },

  async deleteComment(commentId) {
    try {
      await request('DELETE', '/comments/' + commentId);
      this.loadPost();
      wx.showToast({ title: '已删除', icon: 'success' });
    } catch (err) {
      console.error('删除评论失败', err);
      wx.showToast({ title: '删除失败', icon: 'error' });
    }
  },
});
