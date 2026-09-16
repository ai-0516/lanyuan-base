const path = require('path');
const { loadPage } = require('./helpers/load-page');
const {
  normalizeQuery,
  searchParkingTargets,
  calculateViewportTransform,
} = require('../utils/parking');

const pagePath = path.join(__dirname, '../pages/parking/index.js');

describe('parking search', () => {
  test('normalizes user input', () => {
    expect(normalizeQuery('  A 001 ')).toBe('a001');
  });

  test.each([
    ['A001', 'spot', 'A001'],
    ['15#', 'building', '15#'],
    ['西门', 'gate', '西门'],
  ])('finds %s as a %s', (query, type, id) => {
    const result = searchParkingTargets(query);
    expect(result[0]).toMatchObject({ type, id });
  });

  test('limits broad result sets', () => {
    expect(searchParkingTargets('A', 5)).toHaveLength(5);
  });
});

describe('parking map positioning', () => {
  test('centers a target while keeping the map inside viewport bounds', () => {
    const transform = calculateViewportTransform(
      { x: 0.5, y: 0.5 },
      { width: 375, height: 600 },
      { width: 375, height: 478 },
      2.4,
    );

    expect(transform).toEqual({ scale: 2.4, x: -262.5, y: -273.6 });
  });

  test('clamps edge targets to avoid blank map areas', () => {
    const transform = calculateViewportTransform(
      { x: 0, y: 0 },
      { width: 375, height: 600 },
      { width: 375, height: 478 },
      2.4,
    );

    expect(transform).toEqual({ scale: 2.4, x: 0, y: 0 });
  });

  test('selecting a result updates map position and highlight', () => {
    const page = loadPage(pagePath);
    page._viewport = { width: 375, height: 600 };
    page._map = { width: 375, height: 478 };

    page.onSearchInput({ detail: { value: 'A001' } });
    page.onResultTap({ currentTarget: { dataset: { index: 0 } } });

    expect(page.data.selectedTarget).toMatchObject({ id: 'A001', type: 'spot' });
    expect(page.data.selectedTarget.left).toBe('17.9092%');
    expect(page.data.selectedTarget.top).toBe('20.6325%');
    expect(page.data.searchActive).toBe(false);
    expect(page.data.mapScale).toBe(2.4);
  });
});
