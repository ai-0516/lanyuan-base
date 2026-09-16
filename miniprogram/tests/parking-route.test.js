const parkingData = require('../data/parking');
const {
  projectPointToSegment,
  calculateParkingRoute,
  createRouteSegments,
} = require('../utils/parking-route');

describe('parking route geometry', () => {
  test('projects a target onto a road segment', () => {
    expect(projectPointToSegment(
      { x: 5, y: 4 },
      { x: 0, y: 0 },
      { x: 10, y: 0 },
    )).toEqual({ x: 5, y: 0, t: 0.5 });
  });

  test('creates drawable route segments', () => {
    expect(createRouteSegments(
      [{ x: 0, y: 0 }, { x: 50, y: 0 }, { x: 50, y: 100 }],
      100,
      200,
    )).toEqual([
      { left: '0%', top: '0%', width: '50%', angle: '0deg' },
      { left: '50%', top: '0%', width: '100%', angle: '90deg' },
    ]);
  });
});

describe('parking shortest path', () => {
  const oneWayData = {
    imageWidth: 100,
    imageHeight: 100,
    roadGraph: {
      nodes: [{ id: 0, x: 0, y: 50 }, { id: 1, x: 100, y: 50 }],
      edges: [{ from: 0, to: 1, roadIndex: 0, length: 100 }],
      directions: ['oneway'],
    },
  };
  const left = { x: 0.2, y: 0.4 };
  const right = { x: 0.8, y: 0.4 };

  test('follows the direction of a one-way road', () => {
    const route = calculateParkingRoute(left, right, oneWayData);
    expect(route.distanceMeters).toBe(8);
    expect(route.points).toHaveLength(4);
  });

  test('rejects travel against a one-way road', () => {
    expect(calculateParkingRoute(right, left, oneWayData)).toBeNull();
  });

  test('finds a route between real parking targets', () => {
    const start = parkingData.targets.find(target => target.id === 'A001');
    const end = parkingData.targets.find(target => target.id === 'B194');
    const route = calculateParkingRoute(start, end);
    expect(route.distanceMeters).toBeGreaterThan(0);
    expect(route.points.length).toBeGreaterThan(2);
  });
});
