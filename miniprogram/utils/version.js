const { APP_VERSION } = require('./constants');

const ENV_LABELS = {
  develop: '开发版',
  trial: '体验版',
};

function getRuntimeVersion() {
  try {
    const accountInfo = wx.getAccountInfoSync();
    const miniProgram = accountInfo && accountInfo.miniProgram;
    if (!miniProgram) return APP_VERSION;
    if (miniProgram.version) return miniProgram.version;
    return ENV_LABELS[miniProgram.envVersion] || APP_VERSION;
  } catch {
    return APP_VERSION;
  }
}

module.exports = { getRuntimeVersion };
