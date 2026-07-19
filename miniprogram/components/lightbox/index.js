// lightbox 组件 —— 图片全屏预览
// 黑色半透明背景 + swiper 左右滑动 + 页码指示 + 关闭按钮
Component({
  properties: {
    visible: {
      type: Boolean,
      value: false
    },
    images: {
      type: Array,
      value: [] // 所有图片 URL 列表
    },
    current: {
      type: Number,
      value: 0 // 当前显示图片的索引
    }
  },

  methods: {
    /** swiper 滑动切换时更新当前索引 */
    onSwiperChange(e) {
      this.setData({ current: e.detail.current });
    },

    /** 关闭预览 */
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
