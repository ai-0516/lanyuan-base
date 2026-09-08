const { formatDate, formatDateTime, formatTime, friendlyTime, getDaysInMonth } = require('../utils/date')

describe('utils/date', () => {
  beforeEach(() => {
    jest.useFakeTimers().setSystemTime(new Date(2026, 8, 8, 12, 0, 0))
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  test('formats local date and time values', () => {
    const date = new Date(2026, 0, 2, 3, 4, 5)
    expect(formatDateTime(date)).toBe('2026-01-02 03:04:05')
    expect(formatDate(date)).toBe('2026-01-02')
    expect(formatTime(date)).toBe('03:04')
    expect(formatDate('invalid')).toBe('无效日期')
  })

  test.each([
    [30 * 1000, '刚刚'],
    [5 * 60 * 1000, '5分钟前'],
    [3 * 60 * 60 * 1000, '3小时前'],
    [2 * 24 * 60 * 60 * 1000, '2天前'],
  ])('formats a value %i milliseconds ago', (elapsed, expected) => {
    expect(friendlyTime(Date.now() - elapsed)).toBe(expected)
  })

  test('handles leap years', () => {
    expect(getDaysInMonth(2024, 2)).toBe(29)
    expect(getDaysInMonth(2025, 2)).toBe(28)
  })

  test('formats older and future values with absolute timestamps', () => {
    expect(friendlyTime(new Date(2026, 7, 1, 8, 30))).toBe('08-01 08:30')
    expect(friendlyTime(new Date(2025, 7, 1, 8, 30))).toBe('2025-08-01 08:30:00')
    expect(friendlyTime(new Date(2026, 8, 9, 8, 30))).toBe('2026-09-09 08:30:00')
    expect(friendlyTime('invalid')).toBe('无效日期')
  })
})
