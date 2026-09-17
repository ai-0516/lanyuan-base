const { request } = require('../../utils/request');
const { uploadParkingRentalImages, getTempFileURLs, deleteCloudFiles } = require('../../utils/cloud-storage');
const { searchParkingTargets, getParkingBuildings } = require('../../utils/parking');
const { PAGES } = require('../../utils/constants');
const auth = require('../../utils/auth');

Page({
  data: {
    rentalId: null, spotId: '', spotResults: [], nearbyBuilding: '',
    buildingOptions: [], buildingIndex: -1,
    listingType: 'offer', area: '',
    priceMonthly: '', rentalTerm: '', description: '', contact: '',
    tempImages: [], existingImages: [], originalImages: [],
    status: 'active', submitting: false,
  },

  onLoad(options) {
    const listingType = options.type === 'wanted' ? 'wanted' : 'offer';
    this.setData({ listingType, buildingOptions: getParkingBuildings() });
    const returnUrl = options.id
      ? `${PAGES.PARKING_RENTAL_FORM}?id=${options.id}`
      : `${PAGES.PARKING_RENTAL_FORM}?type=${listingType}`;
    if (!auth.checkLogin(returnUrl)) return;
    if (options.id) {
      this.setData({ rentalId: Number(options.id) });
      wx.setNavigationBarTitle({ title: '编辑车位出租' });
      this.loadRental(options.id);
    } else {
      wx.setNavigationBarTitle({ title: listingType === 'wanted' ? '发布车位求租' : '发布车位出租' });
    }
  },

  async loadRental(id) {
    try {
      const item = await request({ url: `/parking-rentals/${id}` });
      if (!item?.is_owner) throw new Error('not owner');
      const contactResult = await request({ url: `/parking-rentals/${id}/contact` });
      wx.setNavigationBarTitle({
        title: item.listing_type === 'wanted' ? '编辑车位求租' : '编辑车位出租',
      });
      const buildingIndex = this.data.buildingOptions.findIndex(
        building => building.label === item.nearby_building,
      );
      this.setData({
        listingType: item.listing_type, spotId: item.spot_id || '', area: item.area,
        nearbyBuilding: item.nearby_building || '',
        buildingIndex,
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
  onBuildingChange(e) {
    const buildingIndex = Number(e.detail.value);
    const building = this.data.buildingOptions[buildingIndex];
    if (!building) return;
    this.setData({ buildingIndex, nearbyBuilding: building.label, area: building.area });
  },
  onFieldInput(e) { this.setData({ [e.currentTarget.dataset.field]: e.detail.value }); },

  chooseImage() {
    const remain = 9 - this.data.existingImages.length - this.data.tempImages.length;
    if (remain <= 0) return;
    wx.chooseMedia({ count: remain, mediaType: ['image'], sizeType: ['compressed'], sourceType: ['album', 'camera'],
      success: res => this.setData({ tempImages: [...this.data.tempImages, ...res.tempFiles.map(file => file.tempFilePath)] }),
    });
  },
  removeImage(e) {
    const tempImages = [...this.data.tempImages];
    tempImages.splice(Number(e.currentTarget.dataset.index), 1);
    this.setData({ tempImages });
  },
  removeExistingImage(e) {
    const existingImages = [...this.data.existingImages];
    existingImages.splice(Number(e.currentTarget.dataset.index), 1);
    this.setData({ existingImages });
  },

  validate() {
    if (this.data.listingType === 'offer') {
      const exactSpot = searchParkingTargets(this.data.spotId, 1)
        .find(item => item.type === 'spot' && item.id === this.data.spotId);
      if (!exactSpot) return '请选择地图中的有效车位';
    } else if (!this.data.nearbyBuilding.trim()) {
      return '请选择期望楼栋';
    }
    if (!this.data.description.trim()) {
      return this.data.listingType === 'offer' ? '请填写出租说明' : '请填写求租需求';
    }
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
          description: this.data.description.trim(), contact: this.data.contact.trim(),
        };
        if (this.data.listingType === 'wanted') {
          updateData.area = this.data.area.trim().toUpperCase();
          updateData.nearby_building = this.data.nearbyBuilding.trim() || null;
        }
        const imagesChanged = this.data.tempImages.length > 0
          || this.data.existingImages.length !== this.data.originalImages.length
          || this.data.existingImages.some(
            (image, index) => image !== this.data.originalImages[index],
          );
        if (imagesChanged) {
          uploadedFileIDs = await uploadParkingRentalImages(this.data.tempImages);
          updateData.images = [...this.data.existingImages, ...uploadedFileIDs];
          updateData.image_urls = await getTempFileURLs(updateData.images);
        }
        const saved = await request({ method: 'PATCH', url: `/parking-rentals/${this.data.rentalId}`, data: updateData });
        if (imagesChanged) {
          const kept = new Set(this.data.existingImages);
          await deleteCloudFiles(this.data.originalImages.filter(image => !kept.has(image)));
        }
        wx.showToast({
          title: saved.moderation_status === 'pending' ? '图片审核中' : '保存成功',
          icon: saved.moderation_status === 'pending' ? 'none' : 'success',
        });
      } else {
        uploadedFileIDs = this.data.listingType === 'offer'
          ? await uploadParkingRentalImages(this.data.tempImages)
          : [];
        const imageURLs = await getTempFileURLs(uploadedFileIDs);
        await request({ method: 'POST', url: '/parking-rentals', data: {
          listing_type: this.data.listingType,
          spot_id: this.data.listingType === 'offer' ? this.data.spotId : null,
          area: this.data.listingType === 'wanted' ? this.data.area.trim().toUpperCase() : null,
          nearby_building: this.data.listingType === 'wanted'
            ? this.data.nearbyBuilding.trim() || null
            : null,
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
    this.setData({ submitting: true });
    try {
      await request({ method: 'PATCH', url: `/parking-rentals/${this.data.rentalId}`, data: { status: 'inactive' } });
      wx.showToast({ title: '已下架', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 800);
    } catch { wx.showToast({ title: '操作失败', icon: 'none' }); }
    finally { this.setData({ submitting: false }); }
  },
});
