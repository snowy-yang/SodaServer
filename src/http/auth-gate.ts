// 全局调用秘钥鉴权 —— AUTH_KEY 环境变量启用（未设置则完全不鉴权）
// 验证头统一为 X-Server-Key: <AUTH_KEY>；全部路径（含文档页/健康检查/404）都要求秘钥，
// 未授权请求一律 401，避免被扫描器区分出接口存在性（404/401 差异）。
// CORS 预检（OPTIONS）无响应体、不泄露内容，豁免。
//
// /stream/* 流代理例外：播放器/下载器直接 GET 媒体地址、无法携带自定义头，
// 改用短时签名（?exp=&sig=，见 stream-sign.ts）——不落裸秘钥到 URL/日志，签名泄露仅能
// 在时效窗口内拉当条流；能带头的客户端（curl 等）仍可用 X-Server-Key。
import crypto from 'node:crypto'
import type { FastifyRequest, FastifyReply } from 'fastify'
import { verifyStreamSign } from './stream-sign.ts'

const safeEqual = (a: string, b: string) => {
  const bufA = Buffer.from(a)
  const bufB = Buffer.from(b)
  return bufA.length === bufB.length && crypto.timingSafeEqual(bufA, bufB)
}

export const registerAuth = (fastify: any, authKey: string) => {
  fastify.addHook('onRequest', async(request: FastifyRequest, reply: FastifyReply) => {
    if (request.method === 'OPTIONS') return
    const pathname = new URL(request.url, 'http://localhost').pathname

    // /stream/* 流媒体代理：X-Server-Key 优先，或短时签名 ?exp=&sig=（播放器拉流兼容）
    if (pathname.startsWith('/stream/')) {
      const header = String(request.headers['x-server-key'] || '')
      const q = request.query as any
      const ok = (header && safeEqual(header, authKey))
        || (!header && verifyStreamSign(authKey, pathname, q?.exp, q?.sig))
      if (ok) return
      return reply.code(401).send({ code: 401, msg: '无效或缺少调用秘钥：X-Server-Key 头，或 /url 返回流地址自带的 exp/sig 签名' })
    }

    const provided = String(request.headers['x-server-key'] || '')
    if (!provided || !safeEqual(provided, authKey)) {
      // 统一 401 文案（不区分路径存在与否，防扫描探测）
      return reply.code(401).send({ code: 401, msg: '无效或缺少调用秘钥：请在 X-Server-Key 请求头携带' })
    }
  })
}
