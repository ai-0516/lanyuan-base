/**
 * 兰园小程序 — 时间格式化工具
 */

/**
 * 补零（个位数前面补 0）
 * @param {number} n
 * @returns {string}
 */
function padZero(n) {
  return n < 10 ? '0' + n : '' + n
}

/**
 * 格式化为 yyyy-MM-dd HH:mm:ss
 * @param {Date|number|string} date - Date 对象 / 时间戳 / ISO 字符串
 * @returns {string}
 */
function formatDateTime(date) {
  const d = new Date(date)
  if (isNaN(d.getTime())) return '无效日期'

  const y = d.getFullYear()
  const M = padZero(d.getMonth() + 1)
  const D = padZero(d.getDate())
  const h = padZero(d.getHours())
  const m = padZero(d.getMinutes())
  const s = padZero(d.getSeconds())

  return `${y}-${M}-${D} ${h}:${m}:${s}`
}

/**
 * 格式化为 yyyy-MM-dd
 * @param {Date|number|string} date
 * @returns {string}
 */
function formatDate(date) {
  const d = new Date(date)
  if (isNaN(d.getTime())) return '无效日期'

  const y = d.getFullYear()
  const M = padZero(d.getMonth() + 1)
  const D = padZero(d.getDate())

  return `${y}-${M}-${D}`
}

/**
 * 格式化为 HH:mm
 * @param {Date|number|string} date
 * @returns {string}
 */
function formatTime(date) {
  const d = new Date(date)
  if (isNaN(d.getTime())) return '无效日期'

  const h = padZero(d.getHours())
  const m = padZero(d.getMinutes())

  return `${h}:${m}`
}

/**
 * 友好时间显示（相对时间）
 *
 * 规则：
 *   <1分钟 → "刚刚"
 *   <1小时 → "x分钟前"
 *   <24小时 → "x小时前"
 *   <7天 → "x天前"
 *   今年 → "MM-DD HH:mm"
 *   更早 → "yyyy-MM-dd HH:mm"
 *
 * @param {Date|number|string} date
 * @returns {string}
 */
function friendlyTime(date) {
  const d = new Date(date)
  if (isNaN(d.getTime())) return '无效日期'

  const now = Date.now()
  const diff = now - d.getTime()

  if (diff < 0) return formatDateTime(d) // 未来时间按完整格式显示

  const seconds = Math.floor(diff / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)

  if (seconds < 60) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`

  const nowYear = new Date().getFullYear()
  const thisYear = d.getFullYear() === nowYear

  if (thisYear) {
    const M = padZero(d.getMonth() + 1)
    const D = padZero(d.getDate())
    const h = padZero(d.getHours())
    const m = padZero(d.getMinutes())
    return `${M}-${D} ${h}:${m}`
  }

  return formatDateTime(d)
}

/**
 * 获取某月的天数
 * @param {number} year
 * @param {number} month (1-12)
 * @returns {number}
 */
function getDaysInMonth(year, month) {
  return new Date(year, month, 0).getDate()
}

module.exports = {
  formatDateTime,
  formatDate,
  formatTime,
  friendlyTime,
  getDaysInMonth,
}
