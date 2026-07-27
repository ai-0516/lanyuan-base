// like-button 组件 —— 点赞按钮
// 已点赞 → DELETE 取消，未点赞 → POST 点赞
const request = require('../../utils/request');

Component({
  properties: {
    postId: {
      type: Number,
      value: 0
    },
    liked: {
      type: Boolean,
      value: false // 是否已点赞
    },
    count: {
      type: Number,
      value: 0 // 点赞数
    }
  },

  methods: {
    /** 点击切换点赞状态 */
    onToggle() {
      const postId = this.data.postId;
      if (!postId) return;

      const isLiked = this.data.liked;
      request({
        url: `/posts/${postId}/like`,
        method: isLiked ? 'DELETE' : 'POST'
      })
        .then(res => {
          // res 已被 request 自动解包为 { liked, likeCount }
          const newLiked = res.liked !== undefined ? res.liked : !isLiked;
          const newCount = res.likeCount || this.data.count;
          this.setData({
            liked: newLiked,
            count: newCount
          });
          // 触发 change 事件供父组件同步状态
          this.triggerEvent('change', { liked: newLiked, count: newCount });
        })
        .catch(() => {});
    }
  }
});
