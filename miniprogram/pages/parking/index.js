const parkingData = require('../../data/parking');
const { PARKING_MAP_URL } = require('../../utils/constants');
const { searchParkingTargets, calculateViewportTransform } = require('../../utils/parking');

const DEFAULT_SCALE = 1;
const FOCUS_SCALE = 2.4;

Page({
  data: {
    mapUrl: PARKING_MAP_URL,
    mapReady: false,
    mapError: false,
    mapWidth: 0,
    mapHeight: 0,
    mapX: 0,
    mapY: 0,
    mapScale: DEFAULT_SCALE,
    query: '',
    results: [],
    searchActive: false,
    selectedTarget: null,
  },

  onShow() {
    this.getTabBar?.()?.setData({ selected: 2 });
  },

  onReady() {
    this.measureViewport();
  },

  measureViewport() {
    wx.createSelectorQuery()
      .select('#parking-map-viewport')
      .boundingClientRect(rect => {
        if (!rect) return;
        const mapWidth = rect.width;
        const mapHeight = mapWidth * parkingData.imageHeight / parkingData.imageWidth;
        this._viewport = { width: rect.width, height: rect.height };
        this._map = { width: mapWidth, height: mapHeight };
        this._fullViewPosition = {
          x: Math.max(0, (rect.width - mapWidth) / 2),
          y: Math.max(0, (rect.height - mapHeight) / 2),
        };
        this._mapScale = DEFAULT_SCALE;
        this.setData({
          mapWidth,
          mapHeight,
          mapX: this._fullViewPosition.x,
          mapY: this._fullViewPosition.y,
          mapScale: DEFAULT_SCALE,
        });
      })
      .exec();
  },

  onMapLoad() {
    this.setData({ mapReady: true, mapError: false });
  },

  onMapError() {
    this.setData({ mapReady: false, mapError: true });
  },

  onSearchFocus() {
    this.setData({ searchActive: true });
  },

  onSearchInput(e) {
    const query = e.detail.value;
    this.setData({
      query,
      results: searchParkingTargets(query),
      searchActive: true,
    });
  },

  clearSearch() {
    this.setData({ query: '', results: [], searchActive: false });
  },

  onResultTap(e) {
    const target = this.data.results[Number(e.currentTarget.dataset.index)];
    if (target) this.focusTarget(target);
  },

  focusTarget(target) {
    if (!this._viewport || !this._map) return;
    const transform = calculateViewportTransform(target, this._viewport, this._map, FOCUS_SCALE);
    this._mapScale = transform.scale;
    this.setData({
      mapX: transform.x,
      mapY: transform.y,
      mapScale: transform.scale,
      selectedTarget: {
        ...target,
        left: `${target.x * 100}%`,
        top: `${target.y * 100}%`,
      },
      searchActive: false,
    });
  },

  onScale(e) {
    if (e.detail && e.detail.scale) this._mapScale = e.detail.scale;
  },

  zoomBy(delta) {
    const scale = Math.max(1, Math.min(4, (this._mapScale || DEFAULT_SCALE) + delta));
    this._mapScale = scale;
    this.setData({ mapScale: scale });
  },

  onZoomIn() {
    this.zoomBy(0.4);
  },

  onZoomOut() {
    this.zoomBy(-0.4);
  },

  resetMap() {
    const position = this._fullViewPosition || { x: 0, y: 0 };
    this._mapScale = DEFAULT_SCALE;
    this.setData({
      mapX: position.x,
      mapY: position.y,
      mapScale: DEFAULT_SCALE,
      selectedTarget: null,
    });
  },
});
