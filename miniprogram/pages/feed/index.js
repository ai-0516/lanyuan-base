// 发现页 - 帖子信息流
const { request } = require('../../utils/request');

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
    /** 当前评论的帖子数据 */
    commentSheetPost: null,
    /** 评论输入框文字 */
    commentText: '',
    /** 是否可以发送 */
    canSend: false,
    /** 当前用户头像 */
    userAvatar: '',
  },

  onLoad() {
    this.loadPosts(true);
    // 读取当前用户头像
    const userInfo = wx.getStorageSync('userInfo') || {};
    if (userInfo.avatar) {
      this.setData({ userAvatar: userInfo.avatar });
    }
  },

  /** 格式化时间（传入 ISO 字符串，返回相对时间或日期） */
  formatTime(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = Date.now();
    const diff = now - date.getTime();

    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;

    if (diff < minute) {
      return '刚刚';
    } else if (diff < hour) {
      return Math.floor(diff / minute) + '分钟前';
    } else if (diff < day) {
      return Math.floor(diff / hour) + '小时前';
    } else if (diff < 7 * day) {
      return Math.floor(diff / day) + '天前';
    } else {
      const m = (date.getMonth() + 1).toString().padStart(2, '0');
      const d = date.getDate().toString().padStart(2, '0');
      return m + '/' + d;
    }
  },

  /** 获取点赞者文本（逗号分隔昵称） */
  getLikersText(likers) {
    if (!likers || likers.length === 0) return '';
    return likers.map(item => item.nickname).join('，');
  },

  /** 加载帖子列表 */
  async loadPosts(reset = false) {
    const page = reset ? 1 : this.data.page;
    const size = this.data.pageSize;

    try {
      const result = await request('GET', '/posts?page=' + page + '&size=' + size);

      const newPosts = (result.items || result.records || result || []).map(post => ({
        ...post,
        displayComments: (post.comments || []).slice(0, 3),
        likersText: (post.likers || []).map(l => l.nickname).join('，'),
        displayTime: this.formatTime(post.created_at),
        displayAvatar: post.user?.avatar || `https://i.pravatar.cc/80?img=${(post.user?.id || 1) % 70}`,
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
      this.setData({
        loading: false,
        loadingMore: false,
        refresherTriggered: false,
      });
    }
  },

  /** 下拉刷新 */
  onPullDownRefresh() {
    this.setData({ refresherTriggered: true });
    this.loadPosts(true);
  },

  /** 上拉加载更多 */
  onLoadMore() {
    if (this.data.loadingMore || !this.data.hasMore) return;
    this.setData({ loadingMore: true });
    this.loadPosts(false);
  },

  /** 切换三点菜单（点击 dots 时滑出/收起操作面板） */
  toggleActions(e) {
    const faId = e.currentTarget.dataset.faId;
    // 如果已打开同一点击收起，否则打开并关闭其他
    this.setData({
      actionOpenId: this.data.actionOpenId === faId ? '' : faId,
    });
  },

  /** 点赞/取消点赞 */
  async toggleLike(e) {
    const postId = e.currentTarget.dataset.postId;
    // 关闭当前滑出面板
    const faId = e.currentTarget.dataset.faId;
    if (faId && this.data.actionOpenId === faId) {
      this.setData({ actionOpenId: '' });
    }
    const posts = [...this.data.posts];
    const index = posts.findIndex(p => p.id === postId);
    if (index === -1) return;

    const post = posts[index];
    const liked = !post.liked;
    const userInfo = wx.getStorageSync('userInfo') || {};
    const currentNickname = userInfo.nickname || '';

    try {
      // 乐观更新：切换 liked 状态，同时更新 likers 名单
      let newLikers = [...(post.likers || [])];
      if (liked) {
        // 点赞 → 添加当前用户到列表
        if (currentNickname && !newLikers.find(l => l.nickname === currentNickname)) {
          newLikers.push({ id: userInfo.id || 0, nickname: currentNickname, avatar: userInfo.avatar || '' });
        }
      } else {
        // 取消赞 → 从列表移除
        newLikers = newLikers.filter(l => l.nickname !== currentNickname);
      }

      posts[index] = {
        ...post,
        liked,
        like_count: liked ? (post.like_count || 0) + 1 : Math.max(0, (post.like_count || 0) - 1),
        likers: newLikers,
        likersText: newLikers.map(l => l.nickname).join('，'),
      };
      this.setData({ posts });

      await request('POST', '/posts/' + postId + '/like');
    } catch (err) {
      // 回滚
      posts[index] = post;
      this.setData({ posts });
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  },

  /** 打开图片预览 */
  openLightbox(e) {
    const { images, index } = e.currentTarget.dataset;
    if (images && images.length > 0) {
      wx.previewImage({ urls: images, current: images[index || 0] });
    }
  },

  /** 打开评论弹窗 */
  async openCommentSheet(e) {
    const postId = e.currentTarget.dataset.postId;
    // 从帖子列表中找到对应帖子
    const post = this.data.posts.find(p => p.id === postId);
    if (!post) return;

    // 关闭三点菜单
    this.setData({ actionOpenId: '' });

    // 准备弹窗数据：所有评论加上 displayTime
    const allComments = (post.comments || []).map(c => ({
      ...c,
      displayTime: this.formatTime(c.created_at),
    }));

    this.setData({
      commentSheetOpen: true,
      commentSheetPost: {
        id: post.id,
        nickname: post.user?.nickname || '',
        allComments,
      },
      commentText: '',
      canSend: false,
    });
  },

  /** 关闭评论弹窗 */
  closeCommentSheet() {
    this.setData({ commentSheetOpen: false, commentSheetPost: null, commentText: '', canSend: false });
  },

  /** 评论输入 */
  onCommentInput(e) {
    const val = e.detail.value;
    this.setData({ commentText: val, canSend: val.trim().length > 0 });
  },

  /** 发送评论 */
  async sendComment() {
    const text = this.data.commentText.trim();
    if (!text || !this.data.commentSheetPost) return;

    const postId = this.data.commentSheetPost.id;
    this.setData({ commentText: '' });

    try {
      const newComment = await request('POST', '/posts/' + postId + '/comments', { content: text });

      // 构建新评论对象
      const userInfo = wx.getStorageSync('userInfo') || {};
      const commentObj = {
        id: newComment.id || Date.now(),
        user: { id: userInfo.id, nickname: userInfo.nickname || '我', avatar: userInfo.avatar || '' },
        content: text,
        reply_to: null,
        created_at: new Date().toISOString(),
        displayTime: '刚刚',
      };

      // 更新弹窗内评论列表
      const post = this.data.commentSheetPost;
      const updatedAllComments = [...(post.allComments || []), commentObj];
      const updatedPost = { ...post, allComments: updatedAllComments };

      // 更新帖子列表中的评论数据
      const posts = [...this.data.posts];
      const pIndex = posts.findIndex(p => p.id === postId);
      if (pIndex !== -1) {
        const oldPost = posts[pIndex];
        const updatedComments = [...(oldPost.comments || []), commentObj];
        posts[pIndex] = {
          ...oldPost,
          comments: updatedComments,
          comment_count: (oldPost.comment_count || 0) + 1,
          displayComments: updatedComments.slice(0, 3),
        };
      }

      this.setData({
        commentSheetPost: updatedPost,
        posts,
        commentText: '',
        canSend: false,
      });

      // 发送成功后关闭弹窗
      this.closeCommentSheet();

      wx.showToast({ title: '发送成功', icon: 'success' });
    } catch (err) {
      console.error('发送评论失败', err);
      wx.showToast({ title: '发送失败', icon: 'error' });
      this.setData({ commentText: text }); // 恢复输入文字
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

  /** 跳转个人主页 */
  goToProfile(e) {
    const userId = e.currentTarget.dataset.userId;
    wx.navigateTo({ url: '/pages/profile/index?userId=' + userId });
  },

  /** 查看帖子详情 */
  viewPost(e) {
    const postId = e.currentTarget.dataset.postId;
    wx.navigateTo({ url: '/pages/post-detail/index?postId=' + postId });
  },
});
