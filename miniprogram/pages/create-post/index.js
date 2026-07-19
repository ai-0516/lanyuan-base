const { request, BASE_URL } = require('../../utils/request');

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

    try {
      const token = wx.getStorageSync('token') || '';
      let uploadedUrls = [];

      // 上传图片（逐张用 wx.uploadFile 发送 multipart）
      if (this.data.tempImages.length > 0) {
        const uploadPromises = this.data.tempImages.map(filePath => {
          return new Promise((resolve, reject) => {
            wx.uploadFile({
              url: BASE_URL + '/upload/images',
              filePath,
              name: 'files',
              header: { 'Authorization': `Bearer ${token}` },
              success: (res) => {
                try {
                  const body = JSON.parse(res.data);
                  if (body.code === 0) {
                    const urls = body.data && body.data.urls;
                    resolve(urls ? urls[0] : body.data || '');
                  } else {
                    reject(new Error(body.message || '上传失败'));
                  }
                } catch (e) {
                  reject(e);
                }
              },
              fail: reject,
            });
          });
        });
        const results = await Promise.all(uploadPromises);
        uploadedUrls = results.filter(Boolean);
      }

      // 发布帖子
      await request({ method: 'POST', url: '/posts', data: { content: this.data.content, images: uploadedUrls } });

      wx.showToast({ title: '发布成功', icon: 'success' });
      setTimeout(() => {
        wx.switchTab({ url: '/pages/feed/index' });
      }, 1000);
    } catch (err) {
      console.error('发布失败', err);
      wx.showToast({ title: '发布失败', icon: 'error' });
    } finally {
      this.setData({ publishing: false });
    }
  },
});
