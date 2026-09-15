const path = require('path');
const { loadComponent } = require('./helpers/load-component');

const componentPath = path.join(__dirname, '../custom-tab-bar/index.js');

describe('custom tab bar login interception', () => {
  test('opens login directly without switching to a protected tab for guests', () => {
    const component = loadComponent(componentPath);

    component.onTabTap({ currentTarget: { dataset: { index: 0 } } });

    expect(wx.setStorageSync).toHaveBeenCalledWith('login_return_url', '/pages/ai-chat/index');
    expect(wx.navigateTo).toHaveBeenCalledWith({ url: '/pages/login/index' });
    expect(wx.switchTab).not.toHaveBeenCalled();
  });

  test('switches tabs immediately when the destination is public or user is logged in', () => {
    const component = loadComponent(componentPath);

    component.onTabTap({ currentTarget: { dataset: { index: 1 } } });
    expect(wx.switchTab).toHaveBeenLastCalledWith({ url: '/pages/feed/index' });

    wx.__storage.set('token', 'valid-token');
    component.onTabTap({ currentTarget: { dataset: { index: 2 } } });
    expect(wx.switchTab).toHaveBeenLastCalledWith({ url: '/pages/profile/index' });
  });
});
