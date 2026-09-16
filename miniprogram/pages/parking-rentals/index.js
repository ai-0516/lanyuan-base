const { request } = require('../../utils/request');
const auth = require('../../utils/auth');
const { PAGES, TAB_PAGES, STORAGE_KEYS } = require('../../utils/constants');

const PRICE_RANGES = [
  { label: '不限价格', min: '', max: '' },
  { label: '300元以下', min: '', max: 300 },
  { label: '300-500元', min: 300, max: 500 },
  { label: '500元以上', min: 500, max: '' },
];

Page({
  data: {
    items: [], loading: false, area: '', building: '', priceIndex: 0,
    priceRanges: PRICE_RANGES, mine: false, hasMore: true, page: 1,
  },

  onLoad() { this.loadRentals(true); },
  onShow() { if (this._loaded) this.loadRentals(true); this._loaded = true; },
  onPullDownRefresh() { this.loadRentals(true).finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() { if (this.data.hasMore) this.loadRentals(false); },

  async loadRentals(reset) {
    if (this.data.loading) return;
    const page = reset ? 1 : this.data.page;
    const range = PRICE_RANGES[this.data.priceIndex];
    const query = [
      `page=${page}`, 'size=20', `mine=${this.data.mine}`,
      this.data.area && `area=${encodeURIComponent(this.data.area)}`,
      this.data.building && `nearby_building=${encodeURIComponent(this.data.building)}`,
      range.min !== '' && `min_price=${range.min}`,
      range.max !== '' && `max_price=${range.max}`,
    ].filter(Boolean).join('&');
    this.setData({ loading: true });
    try {
      const result = await request({ url: `/parking-rentals?${query}` });
      const nextItems = (result.items || []).map(item => ({
        ...item,
        statusText: item.status === 'active' ? '出租中' : '已下架',
        moderationText: item.moderation_status === 'pending' ? '审核中' : item.moderation_status === 'rejected' ? '未通过' : '',
      }));
      this.setData({
        items: reset ? nextItems : [...this.data.items, ...nextItems],
        page: page + 1,
        hasMore: nextItems.length === 20,
      });
    } catch (error) {
      console.error('加载出租信息失败', error);
      wx.showToast({ title: '加载失败', icon: 'none' });
    } finally { this.setData({ loading: false }); }
  },

  onAreaInput(e) { this.setData({ area: e.detail.value.toUpperCase() }); },
  onBuildingInput(e) { this.setData({ building: e.detail.value }); },
  onPriceChange(e) { this.setData({ priceIndex: Number(e.detail.value) }, () => this.loadRentals(true)); },
  applyFilters() { this.loadRentals(true); },
  toggleMine() {
    if (!this.data.mine && !auth.checkLogin(PAGES.PARKING_RENTALS)) return;
    this.setData({ mine: !this.data.mine }, () => this.loadRentals(true));
  },
  createRental() {
    if (auth.checkLogin(PAGES.PARKING_RENTAL_FORM)) wx.navigateTo({ url: PAGES.PARKING_RENTAL_FORM });
  },
  editRental(e) { wx.navigateTo({ url: `${PAGES.PARKING_RENTAL_FORM}?id=${e.currentTarget.dataset.id}` }); },
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
