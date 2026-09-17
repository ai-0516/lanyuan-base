const path = require('path');
const { loadPage } = require('./helpers/load-page');
const { STORAGE_KEYS } = require('../utils/constants');

const formPath = path.join(__dirname, '../pages/parking-rental-form/index.js');
const listPath = path.join(__dirname, '../pages/parking-rentals/index.js');

describe('parking rental form', () => {
  test('only accepts a real parking spot and complete rental fields', () => {
    const page = loadPage(formPath);
    page.setData({
      spotId: 'B194', description: '固定车位', contact: 'wx-owner',
    });
    expect(page.validate()).toBe('');

    page.setData({ spotId: 'B9999' });
    expect(page.validate()).toBe('请选择地图中的有效车位');
  });

  test('wanted listing requires a building but not a specific parking spot', () => {
    const page = loadPage(formPath);
    page.setData({ buildingOptions: [{ label: '6#楼', area: 'B' }] });
    page.onBuildingChange({ detail: { value: '0' } });
    page.setData({
      listingType: 'wanted', description: '希望离 6 号楼近一些',
      contact: 'wx-renter',
    });
    expect(page.validate()).toBe('');
    expect(page.data.nearbyBuilding).toBe('6#楼');
    expect(page.data.area).toBe('B');

    page.setData({ nearbyBuilding: '' });
    expect(page.validate()).toBe('请选择期望楼栋');
  });

  test('editing an offer can remove existing images without replacing the rest', () => {
    const page = loadPage(formPath);
    page.setData({ existingImages: ['cloud://one', 'cloud://two'], originalImages: ['cloud://one', 'cloud://two'] });

    page.removeExistingImage({ currentTarget: { dataset: { index: 0 } } });

    expect(page.data.existingImages).toEqual(['cloud://two']);
    expect(page.data.originalImages).toEqual(['cloud://one', 'cloud://two']);
  });
});

describe('parking rental listing type', () => {
  test('opens on wanted listings by default', () => {
    const page = loadPage(listPath);
    expect(page.data.listingType).toBe('wanted');
  });

  test('switches to offer listings', () => {
    const page = loadPage(listPath);
    page.onListingTypeChange({ currentTarget: { dataset: { type: 'offer' } } });

    expect(page.data.listingType).toBe('offer');
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
