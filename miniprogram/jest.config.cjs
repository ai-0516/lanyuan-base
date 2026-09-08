module.exports = {
  clearMocks: true,
  collectCoverageFrom: [
    'utils/{auth,date,request}.js',
    'pages/login/index.js',
    'components/like-button/index.js',
  ],
  coverageDirectory: 'coverage',
  coveragePathIgnorePatterns: ['/node_modules/'],
  coverageThreshold: {
    global: {
      branches: 70,
      functions: 70,
      lines: 70,
      statements: 70,
    },
  },
  modulePathIgnorePatterns: ['<rootDir>/components/towxml/'],
  setupFilesAfterEnv: ['<rootDir>/tests/setup.js'],
  snapshotSerializers: ['miniprogram-simulate/jest-snapshot-plugin'],
  testEnvironment: 'jsdom',
  testMatch: ['<rootDir>/tests/**/*.test.js'],
}
