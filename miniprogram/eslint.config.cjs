const readonly = 'readonly'

module.exports = [
  {
    ignores: [
      'components/towxml/**',
      'coverage/**',
      'node_modules/**',
    ],
  },
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: 'commonjs',
      globals: {
        App: readonly,
        Component: readonly,
        Page: readonly,
        afterEach: readonly,
        beforeEach: readonly,
        console: readonly,
        describe: readonly,
        document: readonly,
        expect: readonly,
        getApp: readonly,
        getCurrentPages: readonly,
        jest: readonly,
        module: readonly,
        process: readonly,
        require: readonly,
        setTimeout: readonly,
        test: readonly,
        wx: readonly,
        __dirname: readonly,
      },
    },
    rules: {
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-undef': 'error',
      'no-unused-vars': ['error', { args: 'after-used', caughtErrors: 'none' }],
    },
  },
]
