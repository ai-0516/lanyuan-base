const { getRuntimeVersion } = require('../../utils/version');

Page({
  data: {
    appVersion: getRuntimeVersion(),
  },
})
