// HTTP 服务器入口（Fastify）—— 汽水音乐独立服务端 API
// 架构与 MusicServer 同构：标准 HTTP 语义 + FastAPI 风格的自动文档（校验与文档均由 JSON Schema 驱动）
//
//   GET /search/qs/:keyword[/:page]     搜索
//   GET /url/qs/:id                     播放地址（登录态为解密流代理地址）
//   GET /lyric/qs/:id                   歌词（LRC/YRC）
//   GET /pic/qs/:id                     封面
//   GET /info/qs/:id                    曲目元数据（seo_track 全量）
//   GET /stream/qs/:id                  解密流代理（明文音频）
//   POST /login/qs/qr                   扫码登录二维码
//   POST /login/qs/qr/check             轮询扫码状态
//   POST /login/qs/sms                  发送短信验证码
//   POST /login/qs/sms/login            短信验证码登录
//   POST /login/qs                      绑定 sessionid
//   DELETE /login/qs                    退出登录
//   GET /login/status                   登录状态
//   GET /sources /health                GET /docs /redoc /openapi.json
//
// 状态码语义：200 成功；422 参数校验失败；404 路由不存在；405 方法不允许；502 上游失败；500 内部错误
// 响应信封：成功 { code: 0, data }；失败 { code: <HTTP状态码>, msg, detail? }

import './env.ts' // 必须最先导入：在任何读取 process.env 的模块求值前加载 .env
import Fastify from 'fastify'
import cors from '@fastify/cors'
import swagger from '@fastify/swagger'
import swaggerUi from '@fastify/swagger-ui'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import qs from '../qs/index.ts'
import { ApiError } from './http/api-error.ts'
import { musicRoutes } from './routes.ts'
import { loginRoutes } from './routes-login.ts'
import { COMPONENT_SCHEMAS } from './openapi/components.ts'
import { redocHtml } from './openapi/ui.ts'

// 默认端口 16690：避开 MusicServer 常驻实例（16688）与 qs-signer/mssdk-sign（8899/16689/16889）
const PORT = process.env.PORT || 16690
const DOCS_PREFIX = '/docs'
const API_TITLE = 'SodaServer 汽水音乐 API'
const API_DESCRIPTION = '汽水音乐独立服务端：搜索、播放地址（含登录态解密完整曲目）、歌词、封面、曲目信息与账号登录。' +
  '搜索与登录态取流依赖 PC 签名（QS_SIGN_SERVER 签名服务或 data/qs-sign.json 固定值，见 qs-signer 仓库）。'

const pkg = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../package.json'), 'utf8'))

const fastify = Fastify({
  logger: false,
  // schema 中的 example 等文档关键字仅供 OpenAPI 使用，关闭 Ajv 严格模式以免校验构建报错
  ajv: { customOptions: { strict: false } },
})
fastify.decorate('qs', qs)

// ---- 共享 schema（领域模型：校验引用与 OpenAPI components 同源） ----
for (const [name, schema] of Object.entries(COMPONENT_SCHEMAS)) {
  fastify.addSchema({ ...schema, $id: name })
}

// ---- 全局中间行为 ----

// 收集全部路由模式（含 :param 参数段），供 not-found 处理器做 405 判定
const routePatterns: { method: any; regex: RegExp }[] = []
fastify.addHook('onRoute', (routeOptions) => {
  if (!routeOptions.url || !routeOptions.method) return
  // :param 段 → [^/]+；通配符 * → .*（如 @fastify/cors 注册的 catch-all 路由）；其余按字面量转义
  const source = routeOptions.url
    .replace(/:[^/]+/g, '\u0000')
    .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    .replace(/\u0000/g, '[^/]+')
    .replace(/\\\*/g, '.*')
  routePatterns.push({ method: routeOptions.method, regex: new RegExp(`^${source}$`) })
})

fastify.register(cors, { origin: '*' })

// ---- 全局限流（per-IP 滑动窗口，RATE_LIMIT=每分钟请求数，0 关闭；默认 120）----
// 先于鉴权计数：401 爆破秘钥同样受限
const rateLimit = Number(process.env.RATE_LIMIT ?? 120)
if (rateLimit > 0) {
  const { registerRateLimit } = await import('./http/rate-limit.ts')
  registerRateLimit(fastify, rateLimit)
}

// ---- 调用秘钥（AUTH_KEY 环境变量启用；未设置则不鉴权）----
// 统一 X-Server-Key 头，全部路径（含文档/健康检查/404）均要求秘钥，防扫描探测；详见 src/http/auth-gate.ts
const authKey = process.env.AUTH_KEY
if (authKey) {
  const { registerAuth } = await import('./http/auth-gate.ts')
  registerAuth(fastify, authKey)
}

fastify.addHook('onRequest', async(_request, reply) => {
  reply.header('Cache-Control', 'no-store')
})

fastify.addHook('onResponse', async(request, reply) => {
  console.log(`[${new Date().toISOString()}] ${request.method} ${request.url} → ${reply.statusCode} (${Math.round(reply.elapsedTime)}ms)`)
})

// 成功响应统一包成 { code: 0, data }；文档页/raw 路由与非 200 响应不包。
// @fastify/swagger-ui 的规范端点 {DOCS_PREFIX}/json 由插件内部注册、无法加 raw 标记，
// 必须豁免：Swagger UI 强制从该地址加载规范，包了信封顶层就没有 openapi 字段，页面直接报错
fastify.addHook('preSerialization', async(request, reply, payload) => {
  if (reply.statusCode !== 200) return payload
  if ((request.routeOptions.config as any)?.raw) return payload
  if (request.routeOptions.url === `${DOCS_PREFIX}/json`) return payload
  if (payload === null || typeof payload === 'object') return { code: 0, data: payload }
  return payload
})

// ---- 错误处理 ----

fastify.setErrorHandler((err: any, request, reply) => {
  if (err instanceof ApiError) {
    const payload: Record<string, any> = { code: err.status, msg: err.message }
    if (err.detail !== undefined) payload.detail = err.detail
    return reply.code(err.status).send(payload)
  }
  // JSON Schema 校验失败（FastAPI 同款 422 语义）
  if (err.validation) {
    return reply.code(422).send({ code: 422, msg: err.message, detail: err.validation })
  }
  console.error(`[${new Date().toISOString()}] ${request.method} ${request.url} 内部错误:`, err)
  return reply.code(500).send({ code: 500, msg: '服务器内部错误' })
})

fastify.setNotFoundHandler((request, reply) => {
  // 路径存在但方法不允许 → 405 + Allow 头（Fastify 默认给 404，这里补齐标准语义）
  // 用模式匹配而非 hasRoute：hasRoute 需要精确 URL，匹配不到 /url/qs/:id 这类参数路由
  const pathname = new URL(request.url, 'http://localhost').pathname
  const allowed = [...new Set(
    routePatterns
      .filter(r => r.regex.test(pathname) && r.method !== request.method)
      // OPTIONS/HEAD 是辅助方法（CORS 预检由 @fastify/cors 的通配路由承接），不应计入 405 判定
      .filter(r => r.method !== 'OPTIONS' && r.method !== 'HEAD')
      .map(r => r.method),
  )]
  if (allowed.length) {
    reply.code(405).header('Allow', allowed.join(', '))
    return reply.send({ code: 405, msg: `方法 ${request.method} 不允许，请使用 ${allowed.join(', ')}` })
  }
  reply.code(404).send({ code: 404, msg: `接口不存在: ${request.url}` })
})

// ---- OpenAPI 文档 ----

fastify.register(swagger, {
  // 组件按 addSchema 的 $id 命名（默认 def-1/def-2 不可读）
  refResolver: {
    buildLocalReference: (json: any, _baseUri: any, _fragment: any, i: number) => json.$id || `def-${i}`,
  },
  openapi: {
    openapi: '3.1.0',
    info: {
      title: API_TITLE,
      description: API_DESCRIPTION,
      version: pkg.version,
    },
    servers: [{ url: '/', description: '当前服务器' }],
    tags: [
      { name: '搜索', description: '歌曲搜索' },
      { name: '歌曲资源', description: '播放地址、歌词、封面、曲目信息' },
      { name: '登录', description: '账号登录（汽水 App 扫码 / 短信验证码 / sessionid 绑定）与状态' },
      { name: '系统', description: '健康检查、平台信息、文档' },
    ],
  },
})

// 注意：插件初始化器会把规范地址强制为 {DOCS_PREFIX}/json（uiConfig 的 url 会被覆盖），
// 真正对外文档化的规范地址是下方带 raw 标记的 /openapi.json
fastify.register(swaggerUi, { routePrefix: DOCS_PREFIX })

fastify.get('/openapi.json', {
  config: { raw: true },
  schema: {
    summary: 'OpenAPI 3.1 规范文档',
    description: '机器可读的接口描述，可用于生成各语言客户端。',
    tags: ['系统'],
  },
}, async() => fastify.swagger())

fastify.get('/redoc', {
  schema: {
    summary: 'ReDoc 阅读版文档',
    tags: ['系统'],
  },
}, async(_request, reply) => reply.type('text/html; charset=utf-8').send(redocHtml({ title: API_TITLE })))

fastify.get('/', {
  schema: {
    summary: 'API 文档入口',
    description: '302 重定向到 Swagger UI 文档页。',
    tags: ['系统'],
  },
}, async(_request, reply) => reply.redirect('/docs'))

// ---- 业务路由 ----

fastify.register(musicRoutes)
fastify.register(loginRoutes)

export const startServer = async(port = PORT) => {
  await fastify.listen({ port: Number(port), host: '0.0.0.0' })
  console.log(`汽水音乐服务端已启动: http://localhost:${port}`)
  console.log(`调用鉴权:   ${authKey ? '已启用（AUTH_KEY，X-Server-Key 头）' : '未启用（设置 AUTH_KEY 环境变量开启）'}`)
  console.log(`限流:       ${rateLimit > 0 ? `${rateLimit} 次/分钟/IP（RATE_LIMIT 可调，0 关闭）` : '未启用'}`)
  console.log(`PC 签名:    ${process.env.QS_SIGN_SERVER ? `签名服务（QS_SIGN_SERVER）` : '未显式配置（启动时探测 16689，兜底 data/qs-sign.json 固定值）'}`)
  console.log(`接口文档:   http://localhost:${port}/docs (Swagger UI)`)
  console.log(`ReDoc 文档: http://localhost:${port}/redoc`)
  console.log(`OpenAPI:    http://localhost:${port}/openapi.json`)
  return fastify
}

const shutdown = (signal) => {
  console.log(`\n收到 ${signal}，正在关闭服务器...`)
  fastify.close().then(() => {
    console.log('服务器已关闭')
    process.exit(0)
  })
  setTimeout(() => process.exit(0), 3000).unref()
}
process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))

// 直接运行时启动
if (process.argv[1] && (process.argv[1].endsWith('server.ts') || process.argv[1].endsWith('src\\server.ts'))) {
  startServer()
}
