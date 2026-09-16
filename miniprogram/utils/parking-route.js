const parkingData = require('../data/parking');

const METERS_PER_PIXEL = 0.1;

function distance(left, right) {
  return Math.hypot(right.x - left.x, right.y - left.y);
}

function projectPointToSegment(point, start, end) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  const rawT = lengthSquared ? ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared : 0;
  const t = Math.max(0, Math.min(1, rawT));
  return { x: start.x + dx * t, y: start.y + dy * t, t };
}

function closestRoadProjection(target, graph, imageWidth, imageHeight) {
  const point = { x: target.x * imageWidth, y: target.y * imageHeight };
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  let closest = null;

  graph.edges.forEach((edge, edgeIndex) => {
    const start = nodes.get(edge.from);
    const end = nodes.get(edge.to);
    if (!start || !end) return;
    const projection = projectPointToSegment(point, start, end);
    const gap = distance(point, projection);
    if (!closest || gap < closest.gap) closest = { ...projection, gap, edgeIndex };
  });

  return closest;
}

function addArc(adjacency, from, to, weight) {
  if (!adjacency.has(from)) adjacency.set(from, []);
  adjacency.get(from).push({ to, weight });
}

function shortestPath(adjacency, start, end) {
  const distances = new Map([[start, 0]]);
  const previous = new Map();
  const pending = new Set(adjacency.keys());
  pending.add(start);
  pending.add(end);

  while (pending.size) {
    let current = null;
    let currentDistance = Infinity;
    pending.forEach(key => {
      const value = distances.get(key) ?? Infinity;
      if (value < currentDistance) {
        current = key;
        currentDistance = value;
      }
    });
    if (current === null || currentDistance === Infinity) break;
    pending.delete(current);
    if (current === end) break;
    (adjacency.get(current) || []).forEach(arc => {
      const candidate = currentDistance + arc.weight;
      if (candidate < (distances.get(arc.to) ?? Infinity)) {
        distances.set(arc.to, candidate);
        previous.set(arc.to, current);
        pending.add(arc.to);
      }
    });
  }

  if (!distances.has(end)) return null;
  const keys = [];
  for (let key = end; key !== undefined; key = previous.get(key)) keys.push(key);
  keys.reverse();
  return { keys, distance: distances.get(end) };
}

function deduplicatePoints(points) {
  return points.filter((point, index) => index === 0 || distance(point, points[index - 1]) > 0.01);
}

function calculateParkingRoute(startTarget, endTarget, data = parkingData) {
  const { imageWidth, imageHeight, roadGraph } = data;
  if (!startTarget || !endTarget || !roadGraph) return null;

  const startPoint = { x: startTarget.x * imageWidth, y: startTarget.y * imageHeight };
  const endPoint = { x: endTarget.x * imageWidth, y: endTarget.y * imageHeight };
  const startProjection = closestRoadProjection(startTarget, roadGraph, imageWidth, imageHeight);
  const endProjection = closestRoadProjection(endTarget, roadGraph, imageWidth, imageHeight);
  if (!startProjection || !endProjection) return null;

  const coordinates = new Map(roadGraph.nodes.map(node => [`node:${node.id}`, node]));
  coordinates.set('route:start', startProjection);
  coordinates.set('route:end', endProjection);
  const adjacency = new Map();

  roadGraph.edges.forEach((edge, edgeIndex) => {
    const points = [
      { key: `node:${edge.from}`, t: 0 },
      { key: `node:${edge.to}`, t: 1 },
    ];
    if (startProjection.edgeIndex === edgeIndex) points.push({ key: 'route:start', t: startProjection.t });
    if (endProjection.edgeIndex === edgeIndex) points.push({ key: 'route:end', t: endProjection.t });
    points.sort((left, right) => left.t - right.t);

    for (let index = 0; index < points.length - 1; index += 1) {
      const from = points[index];
      const to = points[index + 1];
      const segmentLength = edge.length * (to.t - from.t);
      addArc(adjacency, from.key, to.key, segmentLength);
      if (roadGraph.directions[edge.roadIndex] !== 'oneway') {
        addArc(adjacency, to.key, from.key, segmentLength);
      }
    }
  });

  const path = shortestPath(adjacency, 'route:start', 'route:end');
  if (!path) return null;
  const roadPoints = path.keys.map(key => coordinates.get(key)).filter(Boolean);
  const points = deduplicatePoints([startPoint, ...roadPoints, endPoint]);
  const connectorDistance = startProjection.gap + endProjection.gap;
  const distancePixels = path.distance + connectorDistance;

  return {
    points,
    distancePixels,
    distanceMeters: Math.max(1, Math.round(distancePixels * METERS_PER_PIXEL)),
  };
}

function createRouteSegments(points, imageWidth, imageHeight) {
  const segments = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index];
    const end = points[index + 1];
    segments.push({
      left: `${start.x / imageWidth * 100}%`,
      top: `${start.y / imageHeight * 100}%`,
      width: `${distance(start, end) / imageWidth * 100}%`,
      angle: `${Math.atan2(end.y - start.y, end.x - start.x) * 180 / Math.PI}deg`,
    });
  }
  return segments;
}

module.exports = {
  METERS_PER_PIXEL,
  projectPointToSegment,
  closestRoadProjection,
  calculateParkingRoute,
  createRouteSegments,
};
