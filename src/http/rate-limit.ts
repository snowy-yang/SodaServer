// 全局限流 —— per-IP 滑动窗口（内存实现，无额外依赖）
// RATE_LIMIT 环境变量：每 IP 每分钟允许的请求数（默认 120；设 0 关闭）。
// 在鉴权之前计数（401 爆破秘钥同样受限）；超限返回 429 + Retry-After。
// 未授权请求不区分路径统一计数，与 auth-gate 的防扫描策略一致。
import type { FastifyRequest, FastifyReply } from 'fastify'

const WINDOW_MS = 60_000

export const registerRateLimit = (fastify: any, limitPerMinute: number) => {
  const hits = new Map<string, number[]>()
  // 惰性清理：窗口滑动时顺带删除已过期 IP 的空记录，防内存无限增长
  const cleanup = () => {
    const threshold = Date.now() - WINDOW_MS
    for (const [ip, times] of hits) {
      const alive = times.filter(t => t > threshold)
      if (alive.length) hits.set(ip, alive)
      else hits.delete(ip)
    }
  }
  const cleanupTimer = setInterval(cleanup, WINDOW_MS)
  cleanupTimer.unref?.()

  fastify.addHook('onRequest', async(request: FastifyRequest, reply: FastifyReply) => {
    if (request.method === 'OPTIONS') return
    const ip = request.ip || 'unknown'
    const now = Date.now()
    const times = (hits.get(ip) || []).filter(t => t > now - WINDOW_MS)
    times.push(now)
    hits.set(ip, times)
    if (times.length > limitPerMinute) {
      const retryAfter = Math.ceil((times[0] + WINDOW_MS - now) / 1000)
      reply.header('Retry-After', String(Math.max(1, retryAfter)))
      return reply.code(429).send({ code: 429, msg: `请求过于频繁（限 ${limitPerMinute} 次/分钟），请 ${Math.max(1, retryAfter)} 秒后重试` })
    }
  })
}
