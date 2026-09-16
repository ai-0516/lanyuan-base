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

module.exports = {
  TYPE_LABELS,
  normalizeQuery,
  searchParkingTargets,
  calculateViewportTransform,
};
