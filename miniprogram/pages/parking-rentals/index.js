const { request } = require('../../utils/request');
const auth = require('../../utils/auth');
const { PAGES, TAB_PAGES, STORAGE_KEYS } = require('../../utils/constants');


Page({
  data: {
    items: [], loading: false, hasMore: true, page: 1,
    listingType: 'wanted',
  },

  onLoad() { this.loadRentals(true); },
  onShow() { if (this._loaded) this.loadRentals(true); this._loaded = true; },
  onPullDownRefresh() { this.loadRentals(true).finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() { if (this.data.hasMore) this.loadRentals(false); },

  onListingTypeChange(e) {
    const listingType = e.currentTarget.dataset.type;
    if (listingType === this.data.listingType) return;
    this.setData({ listingType, items: [], page: 1, hasMore: true }, () => {
      this.loadRentals(true);
    });
  },

  async loadRentals(reset) {
    if (this.data.loading) return;
    const page = reset ? 1 : this.data.page;
    this.setData({ loading: true });
    try {
      const result = await request({
        url: `/parking-rentals?page=${page}&size=20&listing_type=${this.data.listingType}`,
      });
      const nextItems = (result.items || []).map(item => ({
        ...item,
        moderationText: item.moderation_status === 'pending'
          ? '审核中'
          : item.moderation_status === 'rejected' ? '未通过' : '',
      }));
      this.setData({
        items: reset ? nextItems : [...this.data.items, ...nextItems],
        page: page + 1,
        hasMore: nextItems.length === 20,
      });
    } catch (error) {
      console.error('加载车位租赁信息失败', error);
      wx.showToast({ title: '加载失败', icon: 'none' });
    } finally { this.setData({ loading: false }); }
  },

  createRental() {
    const target = `${PAGES.PARKING_RENTAL_FORM}?type=${this.data.listingType}`;
    if (auth.checkLogin(target)) wx.navigateTo({ url: target });
  },
  editRental(e) { wx.navigateTo({ url: `${PAGES.PARKING_RENTAL_FORM}?id=${e.currentTarget.dataset.id}` }); },
  previewRentalImage(e) {
    const rental = this.data.items.find(item => item.id === Number(e.currentTarget.dataset.id));
    const urls = rental?.images || [];
    const current = urls[Number(e.currentTarget.dataset.index)];
    if (current) wx.previewImage({ current, urls });
  },
  locateRental(e) {
    wx.setStorageSync(STORAGE_KEYS.PARKING_TARGET_ID, e.currentTarget.dataset.spot);
    wx.switchTab({ url: TAB_PAGES.PARKING });
  },
  async showContact(e) {
    const returnUrl = PAGES.PARKING_RENTALS;
    if (!auth.checkLogin(returnUrl)) return;
    try {
      const result = await request({ url: `/parking-rentals/${e.currentTarget.dataset.id}/contact` });
      wx.showModal({ title: '联系方式', content: result.contact, confirmText: '复制',
        success: modal => { if (modal.confirm) wx.setClipboardData({ data: result.contact }); } });
    } catch { wx.showToast({ title: '暂时无法查看', icon: 'none' }); }
  },
});
