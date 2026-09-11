const { APP_VERSION } = require('../utils/constants');

function loadVersion(accountInfo, error) {
  wx.getAccountInfoSync = jest.fn(() => {
    if (error) throw error;
    return accountInfo;
  });
  let version;
  jest.isolateModules(() => {
    version = require('../utils/version');
  });
  return version.getRuntimeVersion();
}

describe('utils/version', () => {
  test('uses the published version reported by the runtime', () => {
    expect(loadVersion({ miniProgram: { envVersion: 'release', version: '1.2.3' } })).toBe('1.2.3');
  });

  test.each([
    ['develop', '开发版'],
    ['trial', '体验版'],
  ])('labels the %s environment when no release version exists', (envVersion, expected) => {
    expect(loadVersion({ miniProgram: { envVersion, version: '' } })).toBe(expected);
  });

  test('falls back to APP_VERSION when runtime information is unavailable', () => {
    expect(loadVersion(null, new Error('unsupported'))).toBe(APP_VERSION);
  });
});
