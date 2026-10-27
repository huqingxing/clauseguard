/**
 * 等待时长的口径（纯函数，node --test 直接覆盖，不需要起浏览器）。
 *
 * 为什么单独拿出来：等待时长是这个系统里唯一有业务含义的数字——队列里超过 2 小时要标黄，
 * 用户端详情页也要显示「已等待多久」。写在组件里就只能靠肉眼看截图验证，
 * 而「差 1 分钟没标黄」这种错在截图上看不出来。
 */

/** 超过这个时长就说明这笔审批在等人，不是正常流转速度。 */
export const OVERDUE_MS = 2 * 60 * 60 * 1000

/**
 * 等待毫秒数。时间戳非法或时区解析失败时返回 0：
 * 这一列是提示不是判据，宁可显示「刚刚」也不要把整页拖成 NaN 天。
 */
export function waitMillis(waitingSince: string, now: number = Date.now()): number {
  const since = Date.parse(waitingSince)
  if (Number.isNaN(since)) return 0
  // 服务器与本机时钟可能差几秒，负数会显示「-3 分钟」，夹到 0。
  return Math.max(0, now - since)
}

export function isOverdue(waitingSince: string, now: number = Date.now()): boolean {
  return waitMillis(waitingSince, now) > OVERDUE_MS
}

/**
 * 人读的等待时长。只保留两级单位：审核者关心「等了多久」，不关心精确到秒；
 * 「3 天 4 小时」里再带上分钟只是噪音。
 */
export function formatWait(waitingSince: string, now: number = Date.now()): string {
  const ms = waitMillis(waitingSince, now)
  const minutes = Math.floor(ms / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    const rest = minutes % 60
    return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`
  }

  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  return restHours ? `${days} 天 ${restHours} 小时` : `${days} 天`
}
