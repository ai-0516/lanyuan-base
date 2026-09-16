const path = require('path');
const { loadPage } = require('./helpers/load-page');
const { STORAGE_KEYS } = require('../utils/constants');

const formPath = path.join(__dirname, '../pages/parking-rental-form/index.js');
const listPath = path.join(__dirname, '../pages/parking-rentals/index.js');

describe('parking rental form', () => {
  test('only accepts a real parking spot and complete rental fields', () => {
    const page = loadPage(formPath);
    page.setData({
      spotId: 'B194', priceMonthly: '420', rentalTerm: '一年起租',
      description: '固定车位', contact: 'wx-owner',
    });
    expect(page.validate()).toBe('');

    page.setData({ spotId: 'B9999' });
    expect(page.validate()).toBe('请选择地图中的有效车位');
  });
});

describe('parking rental map handoff', () => {
  test('stores the selected spot before switching back to parking tab', () => {
    const page = loadPage(listPath);
    page.locateRental({ currentTarget: { dataset: { spot: 'B194' } } });

    expect(wx.getStorageSync(STORAGE_KEYS.PARKING_TARGET_ID)).toBe('B194');
    expect(wx.switchTab).toHaveBeenCalledWith({ url: '/pages/parking/index' });
  });
});
