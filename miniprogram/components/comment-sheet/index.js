// comment-sheet 组件 —— 底部评论弹出层
// 半屏滑出显示评论列表，支持回复和发表新评论
const request = require('../../utils/request');

Component({
  properties: {
    visible: {
      type: Boolean,
      value: false // 控制弹出层显隐
    },
    postId: {
      type: Number,
      value: 0 // 当前帖子 ID
    },
    replyTo: {
      type: Object,
      value: null // { userId, nickname } 待回复的用户
    }
  },

  data: {
    comments: [],  // 评论列表
    inputValue: '' // 输入框内容
  },

  observers: {
    /** 弹出层打开时自动加载评论 */
    visible(val) {
      if (val && this.data.postId) {
        this.fetchComments();
      }
      if (!val) {
        // 关闭时重置状态
        this.setData({ inputValue: '', replyTo: null });
      }
    },
    /** postId 变化且已打开时重新加载 */
    postId(val) {
      if (val && this.data.visible) {
        this.fetchComments();
      }
    }
  },

  methods: {
    /** 获取当前帖子的评论列表 */
    fetchComments() {
      request({
        url: `/posts/${this.data.postId}/comments`,
        method: 'GET'
      })
        .then(res => {
          this.setData({ comments: res.data || [] });
        })
        .catch(() => {});
    },

    /** 点击评论 -> 设置回复目标 */
    onReplyTap(e) {
      const user = e.currentTarget.dataset.user;
      this.setData({
        replyTo: { userId: user.id, nickname: user.nickname }
      });
    },

    /** 发送评论 */
    onSendTap() {
      const content = this.data.inputValue.trim();
      if (!content) return;

      const data = { content };
      // 如果是回复某条评论，带上 parentCommentId
      if (this.data.replyTo) {
        data.parentCommentId = this.data.replyTo.userId;
      }

      request({
        url: `/posts/${this.data.postId}/comments`,
        method: 'POST',
        data
      })
        .then(() => {
          this.setData({ inputValue: '' });
          this.fetchComments();
          this.triggerEvent('refresh'); // 通知父页面刷新帖子内容
        })
        .catch(() => {});
    },

    /** 关闭弹出层 */
    onCloseTap() {
      this.triggerEvent('close');
    },

    /** 点击遮罩关闭 */
    onMaskTap() {
      this.triggerEvent('close');
    },

    /** 阻止事件冒泡 */
    noop() {}
  }
});
