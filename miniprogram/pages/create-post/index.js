// 发布帖子页
const { request } = require('../../utils/request');

Page({
  data: {
    /** 帖子正文 */
    content: '',
    /** 已选择的图片本地临时路径列表 */
    tempImages: [],
    /** 已上传的图片 URL 列表 */
    images: [],
    /** 是否正在发布 */
    publishing: false,
    /** 是否可以发布 */
    canPublish: false,
  },

  /** 输入框内容变化 */
  onContentInput(e) {
    const content = e.detail.value;
    this.setData({
      content,
      canPublish: content.trim().length > 0 || this.data.images.length > 0,
    });
  },

  /** 选择图片 */
  async chooseImage() {
    const remain = 9 - this.data.images.length;
    if (remain <= 0) {
      wx.showToast({ title: '最多选择9张图片', icon: 'none' });
      return;
    }

    try {
      const res = await wx.chooseMedia({
        count: remain,
        mediaType: ['image'],
        sourceType: ['album', 'camera'],
        sizeType: ['compressed'],
      });

      const newTempFiles = res.tempFiles.map(f => f.tempFilePath || f.path);
      this.setData({
        tempImages: [...this.data.tempImages, ...newTempFiles],
      });

      // 自动上传图片
      await this.uploadImages(newTempFiles);
    } catch (err) {
      if (err.errMsg && err.errMsg.includes('cancel')) return;
      console.error('选择图片失败:', err);
      wx.showToast({ title: '选择图片失败', icon: 'none' });
    }
  },

  /** 上传图片到服务器 */
  async uploadImages(tempFiles) {
    try {
      const result = await request('POST', '/upload/images', {
        files: tempFiles,
      });

      const uploadedUrls = result.urls || result || [];
      this.setData({
        images: [...this.data.images, ...uploadedUrls],
        canPublish: this.data.content.trim().length > 0 || this.data.images.length + uploadedUrls.length > 0,
      });
    } catch (err) {
      console.error('上传图片失败:', err);
      wx.showToast({ title: '图片上传失败', icon: 'none' });
      // 移除上传失败的临时文件
      const failedIndexes = tempFiles.map(f => this.data.tempImages.indexOf(f));
      const remainingTemp = this.data.tempImages.filter((_, i) => !failedIndexes.includes(i));
      this.setData({ tempImages: remainingTemp });
    }
  },

  /** 移除某张图片 */
  removeImage(e) {
    const index = e.currentTarget.dataset.index;
    const images = [...this.data.images];
    const tempImages = [...this.data.tempImages];

    images.splice(index, 1);
    tempImages.splice(index, 1);

    this.setData({
      images,
      tempImages,
      canPublish: this.data.content.trim().length > 0 || images.length > 0,
    });
  },

  /** 发布帖子 */
  async onPublish() {
    if (this.data.publishing || !this.data.canPublish) return;

    // 检查内容
    const content = this.data.content.trim();
    if (!content && this.data.images.length === 0) {
      wx.showToast({ title: '请输入内容或选择图片', icon: 'none' });
      return;
    }

    this.setData({ publishing: true });

    try {
      await request('POST', '/posts', {
        content: content,
        images: this.data.images,
      });

      wx.showToast({ title: '发布成功', icon: 'success' });

      // 返回上一页
      setTimeout(() => {
        wx.navigateBack();
      }, 500);
    } catch (err) {
      console.error('发布失败:', err);
      wx.showToast({ title: err.message || '发布失败', icon: 'none' });
    } finally {
      this.setData({ publishing: false });
    }
  },

  /** 取消发布 */
  onCancel() {
    if (this.data.content.trim() || this.data.images.length > 0) {
      wx.showModal({
        title: '提示',
        content: '确定要放弃编辑吗？',
        success: (res) => {
          if (res.confirm) {
            wx.navigateBack();
          }
        },
      });
    } else {
      wx.navigateBack();
    }
  },
});
