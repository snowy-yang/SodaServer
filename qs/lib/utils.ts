// 纯工具函数（自 MusicServer sdk/common/utils.ts 摘取 qs 用到的子集）

export const sizeFormate = size => {
  // https://gist.github.com/thomseddon/3511330
  if (!size) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const number = Math.floor(Math.log(size) / Math.log(1024))
  return `${(size / Math.pow(1024, Math.floor(number))).toFixed(2)} ${units[number]}`
}

/**
 * 将秒数格式化为 mm:ss
 * @param {number} time 秒
 */
export const formatPlayTime = time => {
  if (Number.isNaN(time) || time < 0) time = 0
  const m = Math.trunc(time / 60)
  const s = Math.trunc(time % 60)
  return m == 0 && s == 0 ? '--/--' : `${m < 10 ? '0' + m : m}:${s < 10 ? '0' + s : s}`
}
