const { request } = require('../../utils/request');
const { uploadPostImages, deleteCloudFiles } = require('../../utils/cloud-storage');

Page({
  data: {
    content: '',
    tempImages: [],
    canPublish: false,
    publishing: false,
  },

  onCancel() {
    wx.navigateBack();
  },

  onContentInput(e) {
    const content = e.detail.value;
    this.setData({
      content,
      canPublish: content.trim().length > 0,
    });
  },

  chooseImage() {
    const remain = 9 - this.data.tempImages.length;
    if (remain <= 0) return;
    wx.chooseMedia({
      count: remain,
      mediaType: ['image'],
      sizeType: ['compressed'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const files = res.tempFiles.map(f => f.tempFilePath);
        this.setData({
          tempImages: [...this.data.tempImages, ...files],
        });
      },
    });
  },

  removeImage(e) {
    const { index } = e.currentTarget.dataset;
    const images = [...this.data.tempImages];
    images.splice(index, 1);
    this.setData({ tempImages: images });
  },

  async onPublish() {
    if (!this.data.canPublish || this.data.publishing) return;

    this.setData({ publishing: true });

    let uploadedFileIDs = [];
    try {

      // 图片直传微信云存储，帖子只保存 cloud:// fileID。
      if (this.data.tempImages.length > 0) {
        uploadedFileIDs = await uploadPostImages(this.data.tempImages);
      }

      // 发布帖子
      await request({
        method: 'POST',
        url: '/posts',
        data: { content: this.data.content, images: uploadedFileIDs },
      });

      wx.showToast({ title: '发布成功', icon: 'success' });
      setTimeout(() => {
        wx.switchTab({ url: '/pages/feed/index' });
      }, 1000);
    } catch (err) {
      console.error('发布失败', err);
      await deleteCloudFiles(uploadedFileIDs);
      wx.showToast({ title: '发布失败', icon: 'error' });
    } finally {
      this.setData({ publishing: false });
    }
  },
});
