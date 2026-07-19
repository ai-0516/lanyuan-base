// like-button 组件 —— 点赞按钮
// 显示点赞数和心形图标，点击切换点赞状态
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

      request({
        url: `/posts/${postId}/like`,
        method: 'POST'
      })
        .then(res => {
          const newLiked = res.data.liked;
          const newCount = res.data.count;
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
