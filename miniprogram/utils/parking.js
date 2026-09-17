const parkingData = require('../data/parking');

const TYPE_LABELS = {
  spot: '车位',
  building: '楼栋',
  gate: '出入口',
};

function normalizeQuery(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '');
}

function searchParkingTargets(query, limit = 30) {
  const normalized = normalizeQuery(query);
  if (!normalized) return [];

  return parkingData.targets
    .filter(target => target.searchText.replace(/\s+/g, '').includes(normalized))
    .sort((left, right) => {
      const leftExact = normalizeQuery(left.id) === normalized ? 0 : 1;
      const rightExact = normalizeQuery(right.id) === normalized ? 0 : 1;
      return leftExact - rightExact || left.title.localeCompare(right.title, 'zh-CN', { numeric: true });
    })
    .slice(0, limit)
    .map(target => ({ ...target, typeLabel: TYPE_LABELS[target.type] }));
}

function getParkingBuildings() {
  const spots = parkingData.targets.filter(target => target.type === 'spot');
  return parkingData.targets
    .filter(target => target.type === 'building')
    .map(building => {
      const nearestSpot = spots.reduce((nearest, spot) => {
        const distance = (spot.x - building.x) ** 2 + (spot.y - building.y) ** 2;
        return !nearest || distance < nearest.distance ? { spot, distance } : nearest;
      }, null)?.spot;
      return {
        id: building.id,
        label: building.title,
        area: nearestSpot?.id?.charAt(0) || '',
      };
    })
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-CN', { numeric: true }));
}

function calculateViewportTransform(target, viewport, map, scale = 2.4) {
  const scaledWidth = map.width * scale;
  const scaledHeight = map.height * scale;
  const desiredX = viewport.width / 2 - target.x * scaledWidth;
  const desiredY = viewport.height / 2 - target.y * scaledHeight;
  const minX = Math.min(0, viewport.width - scaledWidth);
  const minY = Math.min(0, viewport.height - scaledHeight);

  return {
    scale,
    x: Math.max(minX, Math.min(0, desiredX)),
    y: Math.max(minY, Math.min(0, desiredY)),
  };
}

function calculateRouteViewportTransform(points, viewport, map, image, maxScale = 3) {
  if (!points.length) return { scale: 1, x: 0, y: 0 };
  const xs = points.map(point => point.x / image.width);
  const ys = points.map(point => point.y / image.height);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const padding = 56;
  const routeWidth = Math.max(1, (maxX - minX) * map.width);
  const routeHeight = Math.max(1, (maxY - minY) * map.height);
  const scale = Math.max(1, Math.min(
    maxScale,
    (viewport.width - padding * 2) / routeWidth,
    (viewport.height - padding * 2) / routeHeight,
  ));
  const scaledWidth = map.width * scale;
  const scaledHeight = map.height * scale;
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const desiredX = viewport.width / 2 - centerX * scaledWidth;
  const desiredY = viewport.height / 2 - centerY * scaledHeight;
  const clamp = (value, viewportSize, contentSize) => {
    if (contentSize <= viewportSize) return (viewportSize - contentSize) / 2;
    return Math.max(viewportSize - contentSize, Math.min(0, value));
  };

  return {
    scale,
    x: clamp(desiredX, viewport.width, scaledWidth),
    y: clamp(desiredY, viewport.height, scaledHeight),
  };
}

module.exports = {
  TYPE_LABELS,
  normalizeQuery,
  searchParkingTargets,
  getParkingBuildings,
  calculateViewportTransform,
  calculateRouteViewportTransform,
};
