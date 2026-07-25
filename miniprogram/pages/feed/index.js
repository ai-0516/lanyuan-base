// 发现页 - 帖子信息流
const { request } = require('../../utils/request');
const { fullUrl } = require('../../utils/constants');

Page({
  data: {
    /** 帖子列表 */
    posts: [],
    /** 当前页码 */
    page: 1,
    /** 每页条数 */
    pageSize: 20,
    /** 是否还有更多数据 */
    hasMore: true,
    /** 首次加载中 */
    loading: true,
    /** 加载更多中 */
    loadingMore: false,
    /** 下拉刷新触发器状态 */
    refresherTriggered: false,
    /** 当前打开的滑出面板（空=关闭，fa-{id}=打开） */
    actionOpenId: '',
    /** 评论弹窗是否打开 */
    commentSheetOpen: false,
    /** 当前评论的帖子 ID */
    commentSheetPostId: null,
    /** 评论输入框文字 */
    commentText: '',
    /** 是否可以发送 */
    canSend: false,
    /** 回复的目标评论 ID（为空即直接评论） */
    replyToId: null,
    /** 回复的目标用户昵称 */
    replyToName: '',
    /** 当前用户 ID */
    currentUserId: 0,
  },

  onLoad() {
    this.loadPosts(true);
    const userInfo = wx.getStorageSync('userInfo') || {};
    if (userInfo.id) {
      this.setData({ currentUserId: userInfo.id });
    }
  },

  /** 每次页面显示时静默刷新第一页 */
  onShow() {
    if (this.data.posts.length > 0) {
      this.loadPosts(true);
    }
  },

  /* ===== 工具方法 ===== */

  formatTime(dateStr) {
    if (!dateStr) return '';
    const utcStr = typeof dateStr === 'string' && !dateStr.endsWith('Z') && !dateStr.includes('+')
      ? dateStr + 'Z' : dateStr;
    const date = new Date(utcStr);
    const now = Date.now();
    const diff = now - date.getTime();
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if (diff < minute) return '刚刚';
    if (diff < hour) return Math.floor(diff / minute) + '分钟前';
    if (diff < day) return Math.floor(diff / hour) + '小时前';
    if (diff < 7 * day) return Math.floor(diff / day) + '天前';
    const m = (date.getMonth() + 1).toString().padStart(2, '0');
    const d = date.getDate().toString().padStart(2, '0');
    return m + '/' + d;
  },

  getLikersText(likers) {
    if (!likers || likers.length === 0) return '';
    return likers.map(item => item.nickname).join('，');
  },

  /* ===== 数据加载 ===== */

  async loadPosts(reset = false) {
    const page = reset ? 1 : this.data.page;
    const size = this.data.pageSize;
    try {
      const result = await request('GET', '/posts?page=' + page + '&size=' + size);
      const newPosts = (result.items || result.records || result || []).map(post => ({
        ...post,
        like_count: (post.likers || []).length,
        comment_count: (post.comments || []).length,
        displayComments: post.comments || [],
        likersText: this.getLikersText(post.likers),
        displayTime: this.formatTime(post.created_at),
        displayAvatar: post.user?.avatar || `https://i.pravatar.cc/80?img=${(post.user?.id || 1) % 70}`,
        displayImages: (post.images || []).map(img => fullUrl(img)),
      }));
      this.setData({
        posts: reset ? newPosts : [...this.data.posts, ...newPosts],
        page: reset ? 2 : page + 1,
        hasMore: (result.total && result.total > (reset ? newPosts.length : this.data.posts.length + newPosts.length)) || newPosts.length >= size,
        loading: false,
        loadingMore: false,
        refresherTriggered: false,
      });
    } catch (err) {
      console.error('加载帖子失败:', err);
      wx.showToast({ title: '加载失败', icon: 'none' });
      this.setData({ loading: false, loadingMore: false, refresherTriggered: false });
    }
  },

  onPullDownRefresh() {
    this.setData({ refresherTriggered: true });
    this.loadPosts(true);
  },

  onLoadMore() {
    if (this.data.loadingMore || !this.data.hasMore) return;
    this.setData({ loadingMore: true });
    this.loadPosts(false);
  },

  /* ===== post-card 组件事件处理 ===== */

  /** 点赞 */
  onPostLike(e) {
    const { postId } = e.detail;
    this._toggleLike(postId);
  },

  /** 三点菜单切换滑出面板 */
  onPostToggleActions(e) {
    const { faId } = e.detail;
    this.setData({
      actionOpenId: this.data.actionOpenId === faId ? '' : faId,
    });
  },

  /** 打开评论弹窗 */
  onPostComment(e) {
    const { postId } = e.detail;
    this._openCommentSheet(postId);
  },

  /** 点击评论 */
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

  /** 图片预览 */
  onPostPreviewImage(e) {
    const { current, urls } = e.detail;
    if (urls && urls.length > 0) {
      wx.previewImage({ urls, current });
    }
  },

  /** 删除帖子 */
  onPostDelete(e) {
    const { postId } = e.detail;
    this._deletePost(postId);
  },

  /* ===== 业务逻辑（内部方法） ===== */

  /** 点赞/取消点赞 */
  async _toggleLike(postId) {
    this.setData({ actionOpenId: '' });
    const posts = [...this.data.posts];
    const index = posts.findIndex(p => p.id === postId);
    if (index === -1) return;
    const post = posts[index];
    const liked = !post.liked;
    const userInfo = wx.getStorageSync('userInfo') || {};
    const currentNickname = userInfo.nickname || '';

    try {
      let newLikers = [...(post.likers || [])];
      if (liked) {
        if (currentNickname && !newLikers.find(l => l.nickname === currentNickname)) {
          newLikers.push({ id: userInfo.id || 0, nickname: currentNickname, avatar: userInfo.avatar || '' });
        }
      } else {
        newLikers = newLikers.filter(l => l.nickname !== currentNickname);
      }
      posts[index] = {
        ...post,
        liked,
        like_count: liked ? (post.like_count || 0) + 1 : Math.max(0, (post.like_count || 0) - 1),
        likers: newLikers,
        likersText: this.getLikersText(newLikers),
      };
      this.setData({ posts });
      await request('POST', '/posts/' + postId + '/like');
    } catch (err) {
      posts[index] = post;
      this.setData({ posts });
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  },

  /** 打开评论弹窗 */
  _openCommentSheet(postId) {
    this.setData({ actionOpenId: '' });
    const post = this.data.posts.find(p => p.id === postId);
    if (!post) return;
    const allComments = (post.comments || []).map(c => ({
      ...c,
      displayTime: this.formatTime(c.created_at),
    }));
    this.setData({
      commentSheetOpen: true,
      commentSheetPostId: postId,
      commentText: '',
      canSend: false,
      replyToId: null,
      replyToName: '',
    });
  },

  /** 关闭评论弹窗 */
  closeCommentSheet() {
    this.setData({
      commentSheetOpen: false,
      commentSheetPostId: null,
      commentText: '',
      canSend: false,
      replyToId: null,
      replyToName: '',
    });
  },

  /** 评论输入 */
  onCommentInput(e) {
    const val = e.detail.value;
    this.setData({ commentText: val, canSend: val.trim().length > 0 });
  },

  /** 发送评论 */
  async sendComment() {
    const text = this.data.commentText.trim();
    if (!text || !this.data.commentSheetPostId || !this.data.canSend) return;
    const postId = this.data.commentSheetPostId;
    this.setData({ commentText: '' });
    try {
      const payload = { content: text };
      if (this.data.replyToId) payload.parent_comment_id = this.data.replyToId;
      const newComment = await request('POST', '/posts/' + postId + '/comments', payload);
      const userInfo = wx.getStorageSync('userInfo') || {};
      const commentObj = {
        id: newComment.id || Date.now(),
        user: { id: userInfo.id, nickname: userInfo.nickname || '我', avatar: userInfo.avatar || '' },
        content: text,
        reply_to: this.data.replyToId ? { nickname: this.data.replyToName } : null,
        created_at: new Date().toISOString(),
        displayTime: '刚刚',
      };
      const posts = [...this.data.posts];
      const pIndex = posts.findIndex(p => p.id === postId);
      if (pIndex !== -1) {
        const oldPost = posts[pIndex];
        const updatedComments = [...(oldPost.comments || []), commentObj];
        posts[pIndex] = {
          ...oldPost,
          comments: updatedComments,
          comment_count: (oldPost.comment_count || 0) + 1,
          displayComments: updatedComments,
        };
      }
      this.setData({ posts });
      this.closeCommentSheet();
      wx.showToast({ title: '发送成功', icon: 'success' });
    } catch (err) {
      console.error('发送评论失败', err);
      wx.showToast({ title: '发送失败', icon: 'error' });
      this.setData({ commentText: text });
    }
  },

  /** 删除帖子 */
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
      this.setData({ posts: this.data.posts.filter(p => p.id !== postId) });
      wx.showToast({ title: '已删除', icon: 'success' });
    } catch (err) {
      console.error('删除失败', err);
      wx.showToast({ title: '删除失败', icon: 'error' });
    }
  },

  /** 删除评论 */
  async deleteComment(commentId) {
    try {
      await request('DELETE', '/comments/' + commentId);
      const posts = [...this.data.posts];
      for (let i = 0; i < posts.length; i++) {
        const post = posts[i];
        const comments = (post.comments || []).filter(c => c.id !== commentId);
        if (comments.length !== (post.comments || []).length) {
          posts[i] = {
            ...post,
            comments,
            comment_count: Math.max(0, (post.comment_count || 0) - 1),
            displayComments: comments,
          };
          break;
        }
      }
      this.setData({ posts });
      wx.showToast({ title: '已删除', icon: 'success' });
    } catch (err) {
      console.error('删除评论失败', err);
      wx.showToast({ title: '删除失败', icon: 'error' });
    }
  },

  /** 关闭所有滑出面板 */
  closeActions() {
    if (this.data.actionOpenId) {
      this.setData({ actionOpenId: '' });
    }
  },

  /** 跳转发布页 */
  goToCreatePost() {
    wx.navigateTo({ url: '/pages/create-post/index' });
  },

  /** 跳转通知页 */
  goToNotifications() {
    wx.navigateTo({ url: '/pages/notifications/index' });
  },
});
