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
  },

  onLoad() {
    this.loadPosts(true);
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

  /** 点赞/取消点赞 */
  async toggleLike(e) {
    const postId = e.currentTarget.dataset.postId;
    const posts = [...this.data.posts];
    const index = posts.findIndex(p => p.id === postId);
    if (index === -1) return;

    const post = posts[index];
    const isLiked = !post.isLiked;

    try {
      // 乐观更新
      posts[index] = {
        ...post,
        isLiked,
        likeCount: isLiked ? (post.likeCount || 0) + 1 : Math.max(0, (post.likeCount || 0) - 1),
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
  openCommentSheet(e) {
    const postId = e.currentTarget.dataset.postId;
    const commentSheet = this.selectComponent('#commentSheet');
    if (commentSheet) {
      commentSheet.open({ postId });
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
