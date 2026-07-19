// image-grid 组件 —— 图片网格自适应布局
// 根据图片数量自动切换排版：1/2/3/4/5-6/7-9 张
Component({
  properties: {
    images: {
      type: Array,
      value: [] // 图片 URL 数组
    }
  },

  data: {
    gridClass: '0' // 用于 WXML 动态切换样式的 class 后缀
  },

  observers: {
    /** 根据图片数量计算 gridClass */
    images(arr) {
      const len = (arr || []).length;
      let cls = '0';
      if (len === 1) cls = '1';
      else if (len === 2) cls = '2';
      else if (len === 3) cls = '3';
      else if (len === 4) cls = '4';
      else if (len <= 6) cls = '5';  // 5-6 张：2×3 网格
      else cls = '7';                 // 7-9 张：3×3 网格
      this.setData({ gridClass: cls });
    }
  },

  methods: {
    /** 点击图片触发预览事件 */
    onImageTap(e) {
      const current = e.currentTarget.dataset.index;
      this.triggerEvent('preview', {
        urls: this.data.images,
        current
      });
    }
  }
});
