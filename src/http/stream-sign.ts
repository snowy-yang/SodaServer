// 流代理签名 —— /stream/* 的无头客户端兼容方案（播放器/下载器直接 GET，无法携带自定义头）
// /url 返回流地址时附加 ?exp=<秒级时间戳>&sig=<HMAC-SHA256(authKey, "path:exp") 前 32 位>
// 与裸 ?key= 的区别：签名值泄露只能在该时效窗口内拉这一条流，不能反推出秘钥、不能调用其它接口
import crypto from 'node:crypto'

// 流地址有效期（毫秒），与 qs 解密流缓存 TTL（10 分钟）对齐；过期后客户端重新 /url 即可
export const STREAM_SIGN_TTL = 10 * 60_000

const hmac = (authKey: string, data: string) =>
  crypto.createHmac('sha256', authKey).update(data).digest('hex').slice(0, 32)

/** 生成流地址的签名参数（path 为不含 query 的路径，如 /stream/qs/123） */
export const signStream = (authKey: string, path: string) => {
  const exp = Math.floor((Date.now() + STREAM_SIGN_TTL) / 1000)
  return `exp=${exp}&sig=${hmac(authKey, `${path}:${exp}`)}`
}

/** 校验流地址签名（timingSafeEqual + 时效窗口） */
export const verifyStreamSign = (authKey: string, path: string, exp: unknown, sig: unknown): boolean => {
  const expNum = Number(exp)
  const sigStr = String(sig || '')
  if (!Number.isFinite(expNum) || !sigStr) return false
  if (Date.now() / 1000 > expNum) return false // 过期即拒（客户端重新 /url 取新地址）
  const expected = Buffer.from(hmac(authKey, `${path}:${expNum}`))
  const provided = Buffer.from(sigStr)
  return expected.length === provided.length && crypto.timingSafeEqual(expected, provided)
}
