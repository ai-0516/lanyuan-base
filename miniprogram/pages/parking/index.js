const parkingData = require('../../data/parking');
const { PARKING_MAP_URL } = require('../../utils/constants');
const {
  searchParkingTargets,
  calculateViewportTransform,
  calculateRouteViewportTransform,
} = require('../../utils/parking');
const { calculateParkingRoute, createRouteSegments } = require('../../utils/parking-route');

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
    routeStart: null,
    routeEnd: null,
    routeSegments: [],
    routeMarkers: [],
    routeDistance: 0,
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

  onSetRouteEndpoint(e) {
    const target = this.data.selectedTarget;
    const role = e.currentTarget.dataset.role;
    if (!target || !['start', 'end'].includes(role)) return;
    const endpoint = {
      type: target.type,
      id: target.id,
      title: target.title,
      subtitle: target.subtitle,
      x: target.x,
      y: target.y,
    };
    const next = {
      routeStart: role === 'start' ? endpoint : this.data.routeStart,
      routeEnd: role === 'end' ? endpoint : this.data.routeEnd,
    };
    if (next.routeStart && next.routeEnd && next.routeStart.type === next.routeEnd.type
      && next.routeStart.id === next.routeEnd.id) {
      wx.showToast({ title: '起点和终点不能相同', icon: 'none' });
      return;
    }
    this.setData(next, () => this.updateRoute());
  },

  updateRoute() {
    const { routeStart, routeEnd } = this.data;
    const routeMarkers = [
      routeStart && { ...routeStart, role: 'start', label: '起', left: `${routeStart.x * 100}%`, top: `${routeStart.y * 100}%` },
      routeEnd && { ...routeEnd, role: 'end', label: '终', left: `${routeEnd.x * 100}%`, top: `${routeEnd.y * 100}%` },
    ].filter(Boolean);

    if (!routeStart || !routeEnd) {
      this.setData({ routeMarkers, routeSegments: [], routeDistance: 0 });
      return;
    }

    const route = calculateParkingRoute(routeStart, routeEnd);
    if (!route) {
      this.setData({ routeMarkers, routeSegments: [], routeDistance: 0 });
      wx.showToast({ title: '起终点之间暂无可用路线', icon: 'none' });
      return;
    }

    const transform = calculateRouteViewportTransform(
      route.points,
      this._viewport,
      this._map,
      { width: parkingData.imageWidth, height: parkingData.imageHeight },
    );
    this._mapScale = transform.scale;
    this.setData({
      routeMarkers,
      routeSegments: createRouteSegments(route.points, parkingData.imageWidth, parkingData.imageHeight),
      routeDistance: route.distanceMeters,
      mapX: transform.x,
      mapY: transform.y,
      mapScale: transform.scale,
      selectedTarget: null,
    });
  },

  swapRoute() {
    const routeStart = this.data.routeEnd;
    const routeEnd = this.data.routeStart;
    this.setData({ routeStart, routeEnd }, () => this.updateRoute());
  },

  clearRoute() {
    this.setData({
      routeStart: null,
      routeEnd: null,
      routeSegments: [],
      routeMarkers: [],
      routeDistance: 0,
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
