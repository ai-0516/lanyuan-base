const { request } = require('../../utils/request');
const { uploadParkingRentalImages, getTempFileURLs, deleteCloudFiles } = require('../../utils/cloud-storage');
const { searchParkingTargets } = require('../../utils/parking');
const { PAGES } = require('../../utils/constants');
const auth = require('../../utils/auth');

Page({
  data: {
    rentalId: null, spotId: '', spotResults: [], nearbyBuilding: '',
    priceMonthly: '', rentalTerm: '', description: '', contact: '',
    tempImages: [], existingImages: [], originalImages: [], replaceImages: false,
    status: 'active', submitting: false,
  },

  onLoad(options) {
    const returnUrl = options.id ? `${PAGES.PARKING_RENTAL_FORM}?id=${options.id}` : PAGES.PARKING_RENTAL_FORM;
    if (!auth.checkLogin(returnUrl)) return;
    if (options.id) {
      this.setData({ rentalId: Number(options.id) });
      wx.setNavigationBarTitle({ title: '编辑车位出租' });
      this.loadRental(options.id);
    }
  },

  async loadRental(id) {
    try {
      const item = await request({ url: `/parking-rentals/${id}` });
      if (!item?.is_owner) throw new Error('not owner');
      const contactResult = await request({ url: `/parking-rentals/${id}/contact` });
      this.setData({
        spotId: item.spot_id, nearbyBuilding: item.nearby_building || '',
        priceMonthly: String(item.price_monthly), rentalTerm: item.rental_term,
        description: item.description, contact: contactResult.contact,
        existingImages: item.images || [], originalImages: item.images || [], status: item.status,
      });
    } catch { wx.showToast({ title: '无法编辑该信息', icon: 'none' }); }
  },

  onSpotInput(e) {
    const spotId = e.detail.value.toUpperCase();
    const spotResults = searchParkingTargets(spotId, 8).filter(item => item.type === 'spot');
    this.setData({ spotId, spotResults });
  },
  selectSpot(e) { this.setData({ spotId: e.currentTarget.dataset.id, spotResults: [] }); },
  onFieldInput(e) { this.setData({ [e.currentTarget.dataset.field]: e.detail.value }); },

  chooseImage() {
    const remain = 9 - this.data.tempImages.length;
    if (remain <= 0 || (this.data.rentalId && !this.data.replaceImages)) return;
    wx.chooseMedia({ count: remain, mediaType: ['image'], sizeType: ['compressed'], sourceType: ['album', 'camera'],
      success: res => this.setData({ tempImages: [...this.data.tempImages, ...res.tempFiles.map(file => file.tempFilePath)] }),
    });
  },
  removeImage(e) {
    const tempImages = [...this.data.tempImages];
    tempImages.splice(Number(e.currentTarget.dataset.index), 1);
    this.setData({ tempImages });
  },

  replaceImages() { this.setData({ replaceImages: true, tempImages: [] }); },
  cancelReplaceImages() { this.setData({ replaceImages: false, tempImages: [] }); },

  validate() {
    const exactSpot = searchParkingTargets(this.data.spotId, 1)
      .find(item => item.type === 'spot' && item.id === this.data.spotId);
    if (!exactSpot) return '请选择地图中的有效车位';
    if (!Number(this.data.priceMonthly) || Number(this.data.priceMonthly) <= 0) return '请填写月租价格';
    if (!this.data.rentalTerm.trim()) return '请填写租期';
    if (!this.data.description.trim()) return '请填写车位说明';
    if (!this.data.contact.trim()) return '请填写联系方式';
    return '';
  },

  async submit() {
    if (this.data.submitting) return;
    const error = this.validate();
    if (error) { wx.showToast({ title: error, icon: 'none' }); return; }
    this.setData({ submitting: true });
    let uploadedFileIDs = [];
    try {
      if (this.data.rentalId) {
        const updateData = {
          nearby_building: this.data.nearbyBuilding.trim() || null,
          price_monthly: Number(this.data.priceMonthly), rental_term: this.data.rentalTerm.trim(),
          description: this.data.description.trim(), contact: this.data.contact.trim(),
        };
        if (this.data.replaceImages) {
          uploadedFileIDs = await uploadParkingRentalImages(this.data.tempImages);
          updateData.images = uploadedFileIDs;
          updateData.image_urls = await getTempFileURLs(uploadedFileIDs);
        }
        const saved = await request({ method: 'PATCH', url: `/parking-rentals/${this.data.rentalId}`, data: updateData });
        if (this.data.replaceImages) await deleteCloudFiles(this.data.originalImages);
        wx.showToast({
          title: saved.moderation_status === 'pending' ? '图片审核中' : '保存成功',
          icon: saved.moderation_status === 'pending' ? 'none' : 'success',
        });
      } else {
        uploadedFileIDs = await uploadParkingRentalImages(this.data.tempImages);
        const imageURLs = await getTempFileURLs(uploadedFileIDs);
        await request({ method: 'POST', url: '/parking-rentals', data: {
          spot_id: this.data.spotId, nearby_building: this.data.nearbyBuilding.trim() || null,
          price_monthly: Number(this.data.priceMonthly), rental_term: this.data.rentalTerm.trim(),
          description: this.data.description.trim(), contact: this.data.contact.trim(),
          images: uploadedFileIDs, image_urls: imageURLs,
        } });
      }
      if (!this.data.rentalId) {
        wx.showToast({ title: uploadedFileIDs.length ? '已提交审核' : '发布成功', icon: 'success' });
      }
      setTimeout(() => wx.navigateBack(), 800);
    } catch (err) {
      console.error('保存出租信息失败', err);
      await deleteCloudFiles(uploadedFileIDs);
      const message = [40010, 40014].includes(err?.data?.code) ? err.data.message : '保存失败';
      wx.showToast({ title: message, icon: 'none' });
    } finally { this.setData({ submitting: false }); }
  },

  async toggleStatus() {
    if (!this.data.rentalId || this.data.submitting) return;
    const status = this.data.status === 'active' ? 'inactive' : 'active';
    this.setData({ submitting: true });
    try {
      await request({ method: 'PATCH', url: `/parking-rentals/${this.data.rentalId}`, data: { status } });
      this.setData({ status });
      wx.showToast({ title: status === 'active' ? '已重新上架' : '已下架', icon: 'success' });
    } catch { wx.showToast({ title: '操作失败', icon: 'none' }); }
    finally { this.setData({ submitting: false }); }
  },
});
