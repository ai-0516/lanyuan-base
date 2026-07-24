// post-card 组件 —— 帖子卡片
// 展示帖子内容、操作栏、点赞用户和评论列表
const request = require('../../utils/request');
const { fullUrl } = require('../../utils/constants');

Component({
  properties: {
    post: {
      type: Object,
      value: {} // { id, user: { nickname, avatar }, content, images[], likeCount, liked, commentCount, comments[], createdAt }
    }
  },

  data: {
    likedUsers: [] // 点赞用户名单（从 API 获取）
  },

  observers: {
    /** 处理图片为完整 URL */
    post(val) {
      if (!val) return;
      const patch = {};
      if (val.user?.avatar) {
        patch['post.user.avatar'] = fullUrl(val.user.avatar);
      }
      if (val.images?.length) {
        patch['post.images'] = val.images.map(fullUrl);
      }
      if (val.comments?.length) {
        patch['post.comments'] = val.comments.map(c => {
          if (c.user?.avatar) c.user.avatar = fullUrl(c.user.avatar);
          return c;
        });
      }
      if (Object.keys(patch).length) {
        this.setData(patch);
      }
    }
  },

  methods: {
    /** 点赞状态变化回调 */
    onLikeChange(e) {
      const { liked, count } = e.detail;
      // 同步更新帖子数据
      this.setData({
        'post.liked': liked,
        'post.likeCount': count
      });
      // 刷新点赞用户列表
      this.fetchLikedUsers();
    },

    /** 获取点赞用户列表 */
    fetchLikedUsers() {
      const postId = this.data.post.id;
      if (!postId) return;
      request({
        url: `/posts/${postId}/likes`,
        method: 'GET'
      })
        .then(res => {
          this.setData({ likedUsers: res.data || [] });
        })
        .catch(() => {});
    },

    /** 点击评论入口 -> 触发 comment 事件，父页面弹出 comment-sheet */
    onCommentTap() {
      this.triggerEvent('comment', { postId: this.data.post.id });
    },

    /** 图片预览代理 */
    onPreviewImage(e) {
      this.triggerEvent('preview', e.detail);
    }
  }
});
