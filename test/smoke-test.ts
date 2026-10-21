// 冒烟测试 —— 验证汽水服务端各接口
// 运行：pnpm test（Node 22.18+ 直接以类型剥离方式运行 .ts）
//
// 覆盖：搜索（签名可用时正向，否则断言 502 提示）、匿名试听/歌词/封面/曲目信息、
//       登录（扫码创建、sms 负例、sessionid 绑定与解绑）、
//       标准 HTTP 语义（404/405/422/302）、文档页、AUTH_KEY 全路径鉴权与流签名、限流 429

import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { spawn } from 'node:child_process'

// 端口策略：默认在随机空闲端口自起独立实例（避免连到 16690 等常驻实例——
// 旧代码会让新用例误报失败）；显式设置 BASE_URL 时测试该外部实例、不再自动启动
const PORT = Number(process.env.PORT || 0)
let BASE = process.env.BASE_URL || ''
const externalServer = Boolean(BASE)
const results: { name: string; ok: boolean; detail?: any }[] = []

const get = (path: string): Promise<any> => new Promise((resolve, reject) => {
  http.get(`${BASE}${path}`, res => {
    let data = ''
    res.on('data', c => { data += c })
    res.on('end', () => {
      try { resolve(JSON.parse(data)) } catch { reject(new Error(`JSON parse failed: ${data.slice(0, 100)}`)) }
    })
  }).on('error', reject)
})

// 原始响应（不解析 JSON，用于 HTML 页与状态码断言）
const getRaw = (path: string): Promise<any> => new Promise((resolve, reject) => {
  http.get(`${BASE}${path}`, res => {
    let data = ''
    res.on('data', c => { data += c })
    res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: data }))
  }).on('error', reject)
})

const postRaw = (path: string): Promise<any> => new Promise((resolve, reject) => {
  const req = http.request(`${BASE}${path}`, { method: 'POST' }, res => {
    let data = ''
    res.on('data', c => { data += c })
    res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: data }))
  })
  req.on('error', reject)
  req.end()
})

// JSON POST（登录等写入接口）
const postJson = (path: string, body: any): Promise<any> => new Promise((resolve, reject) => {
  const payload = JSON.stringify(body)
  const req = http.request(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
  }, res => {
    let data = ''
    res.on('data', c => { data += c })
    res.on('end', () => resolve({
      status: res.statusCode,
      body: (() => { try { return JSON.parse(data) } catch { return null } })(),
    }))
  })
  req.on('error', reject)
  req.write(payload)
  req.end()
})

const test = async(name: string, fn: () => Promise<any>) => {
  try {
    const result = await fn()
    results.push({ name, ok: true, detail: result })
    console.log(`✅ ${name}`)
  } catch (err: any) {
    results.push({ name, ok: false, detail: err.message })
    console.log(`❌ ${name}: ${err.message}`)
  }
}

// 测试数据目录：与真实 data/ 隔离，避免登录用例改写开发者的真实登录态
const TEST_DATA_DIR = path.join(process.cwd(), 'tmp', 'test-data')

// 已启动的全部子实例。无论脚本正常收尾还是中途抛错，退出时统一回收 ——
// 否则异常路径会留下监听端口的孤儿 node
const spawned: any[] = []
process.on('exit', () => {
  for (const proc of spawned) {
    try { proc.kill() } catch { /* 已自行退出 */ }
  }
})

/**
 * 启动本地服务器（指定端口，可覆盖环境变量）并返回进程句柄
 */
const startLocalServer = (port: number, envExtra: Record<string, string> = {}) => new Promise((resolve, reject) => {
  const proc = spawn(process.execPath, ['src/server.ts'], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PORT: String(port),
      DATA_DIR: TEST_DATA_DIR,
      ...envExtra,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let stdout = ''
  proc.stdout.on('data', d => { stdout += d })
  proc.stderr.on('data', d => { stdout += d })

  const timer = setTimeout(() => {
    proc.kill()
    reject(new Error(`启动本地服务器超时（30s）\n--- 子进程输出（末 2000 字）---\n${stdout.slice(-2000) || '(无输出)'}`))
  }, 30000)

  const check = () => {
    // 子进程提前退出（端口占用 / 语法错误等）：立即带输出报错，不再空等到超时
    if (proc.exitCode !== null) {
      clearTimeout(timer)
      return reject(new Error(`本地服务器进程已退出（code ${proc.exitCode}）\n--- 输出（末 2000 字）---\n${stdout.slice(-2000) || '(无输出)'}`))
    }
    // 启用 AUTH_KEY 的实例全路径要求秘钥，探活也须携带
    const headers = envExtra.AUTH_KEY ? { 'X-Server-Key': envExtra.AUTH_KEY } : {}
    http.get(`http://localhost:${port}/health`, { headers }, res => {
      res.resume()
      if (res.statusCode === 200) {
        clearTimeout(timer)
        spawned.push(proc)
        resolve(proc)
      } else {
        setTimeout(check, 200)
      }
    }).on('error', () => setTimeout(check, 200))
  }
  setTimeout(check, 300)
})

let localServer: any = null
if (externalServer) {
  console.log(`测试外部实例 ${BASE}\n`)
} else {
  // 未显式指定 BASE_URL/PORT 时取随机空闲端口
  const freePort = await new Promise<number>(resolve => {
    const srv = net.createServer()
    srv.listen(0, '127.0.0.1', () => {
      const p = (srv.address() as any).port
      srv.close(() => resolve(p))
    })
  })
  const port = PORT || freePort
  BASE = `http://localhost:${port}`
  // 主实例关闭限流：签名正例+登录用例数不少，限流行为由下方独立低阈值实例专项验证
  localServer = await startLocalServer(port, { RATE_LIMIT: '0' })
  console.log(`本地服务器已启动（:${port}）\n`)
}

// ---------- 系统 ----------

await test('健康检查 GET /health', async() => {
  const r = await get('/health')
  if (r.code != 0 || r.data?.status !== 'ok') throw new Error(r.msg || JSON.stringify(r))
  return `uptime ${r.data.uptime}s`
})

await test('平台信息 GET /sources', async() => {
  const r = await get('/sources')
  if (r.code != 0) throw new Error(r.msg)
  const p = r.data?.platforms?.[0]
  if (p?.id !== 'qs' || p?.name !== '汽水音乐') throw new Error(`平台行异常: ${JSON.stringify(p)}`)
  if (!Array.isArray(p.qualities) || !p.qualities.includes('128k')) throw new Error('音质声明异常')
  if (!p.capabilities?.search || !p.capabilities?.url || !p.capabilities?.lyric || !p.capabilities?.pic) throw new Error('能力位异常')
  if (p.capabilities?.boards || p.capabilities?.parse) throw new Error('不应声明 boards/parse')
  return p.qualities.join('/')
})

// ---------- 搜索（依赖 PC 签名） ----------
// 签名可用（QS_SIGN_SERVER 显式配置，或本机 16689 探测到 mssdk-sign）→ 正向断言；
// 不可用 → 断言 502 且 msg 指向签名配置（此时签名相关的取流正例同样跳过）

let signAvailable = false
await test('搜索 qs（签名可用正向 / 不可用 502 提示）', async() => {
  const r = await get(`/search/qs/${encodeURIComponent('稻香')}?limit=2`)
  if (r.code == 0) {
    signAvailable = true
    if (!r.data?.list?.length) throw new Error('空列表')
    if (r.data.source !== 'qs') throw new Error(`source 应为 qs，实际 ${r.data.source}`)
    return `${r.data.list[0].name} / ${r.data.list[0].singer}`
  }
  if (r.code == 502 && /签名/.test(r.msg ?? '')) return `签名未配置（预期降级）: ${r.msg}`
  throw new Error(`code=${r.code} msg=${r.msg}`)
})

await test('搜索分页 GET /search/qs/:keyword/:page', async() => {
  const r = await get(`/search/qs/${encodeURIComponent('稻香')}/1`)
  if (signAvailable) {
    if (r.code != 0) throw new Error(r.msg)
    if (!r.data?.list?.length) throw new Error('空列表')
    return `${r.data.list.length} 条`
  }
  if (r.code == 502) return '签名未配置（预期降级）'
  throw new Error(`code=${r.code} msg=${r.msg}`)
})

// ---------- 歌曲资源（匿名 seo_track 通道，无签名依赖） ----------

// qs 匿名只有 30 秒试听（7079108541549643812 = 陈粒《小半》，2026-10-01 实测可用）
await test('URL qs 试听 128k', async() => {
  const r = await get('/url/qs/7079108541549643812?quality=128k')
  if (r.code != 0) throw new Error(r.msg)
  if (!r.data?.url) throw new Error('无 url')
  return r.data.url.slice(0, 60)
})

await test('歌词 qs', async() => {
  const r = await get('/lyric/qs/7079108541549643812')
  if (r.code != 0) throw new Error(r.msg)
  if (!r.data?.lyric) throw new Error('无歌词')
  return `${r.data.lyric.slice(0, 30)}...`
})

await test('封面 qs', async() => {
  const r = await get('/pic/qs/7079108541549643812')
  if (r.code != 0) throw new Error(r.msg)
  if (!r.data?.url) throw new Error('无 url')
  return r.data.url.slice(0, 60)
})

await test('曲目信息 GET /info/qs/:id', async() => {
  const r = await get('/info/qs/7079108541549643812')
  if (r.code != 0) throw new Error(r.msg)
  const track = r.data?.seo_track?.track
  if (!track?.id) throw new Error('无 seo_track.track')
  return `${track.name} / duration=${track.duration}ms`
})

// ---------- 标准 HTTP 语义 ----------

await test('未知路径 404', async() => {
  const r = await get('/not-exist-path')
  if (r.code != 404) throw new Error(`期望 404，实际 ${r.code}`)
  return r.msg
})

await test('方法不允许 405', async() => {
  const r = await postRaw('/search/qs/abc')
  if (r.status !== 405) throw new Error(`期望 405，实际 ${r.status}`)
  if (!r.headers.allow) throw new Error('缺少 Allow 头')
  return `Allow: ${r.headers.allow}`
})

await test('参数校验失败 422', async() => {
  // page 最小值 1：0 应被 schema 拒绝（422，FastAPI 同款语义）
  const r = await getRaw('/search/qs/abc/0')
  if (r.status !== 422) throw new Error(`期望 422，实际 ${r.status}`)
  return 'page=0 → 422'
})

await test('根路径 302 → /docs', async() => {
  const r = await getRaw('/')
  if (r.status !== 302) throw new Error(`期望 302，实际 ${r.status}`)
  if (!String(r.headers.location).includes('/docs')) throw new Error(`location 异常: ${r.headers.location}`)
  return r.headers.location
})

await test('文档页 GET /docs 与 /openapi.json', async() => {
  const docs = await getRaw('/docs')
  if (docs.status !== 200 || !String(docs.body).includes('swagger')) throw new Error(`/docs 异常 HTTP ${docs.status}`)
  const spec = await get('/openapi.json')
  if (!spec.openapi || !spec.info?.title) throw new Error('openapi.json 缺少规范字段')
  if (spec.info.title !== 'SodaServer 汽水音乐 API') throw new Error(`标题异常: ${spec.info.title}`)
  if (!spec.paths?.['/search/qs/{keyword}']) throw new Error('文档缺少搜索路径')
  return `${Object.keys(spec.paths).length} 条路径`
})

// ---------- 登录 ----------

await test('扫码二维码创建 POST /login/qs/qr', async() => {
  const r = await postJson('/login/qs/qr', {})
  if (r.status !== 200 || r.body?.code !== 0) throw new Error(r.body?.msg ?? `HTTP ${r.status}`)
  if (!r.body.data?.token || !r.body.data?.qrimg?.startsWith('data:image/')) throw new Error('响应不完整')
  // 二维码必须是官方 PC 客户端同款扫码页（旧的 ucenter_web/app/sdk-next 已 404，抖音扫出「无法访问页面」）
  if (!String(r.body.data?.scanUrl ?? '').includes('/light/invoke/scan_login')) throw new Error(`二维码内容非官方扫码页: ${r.body.data?.scanUrl}`)
  return `scanApp=${r.body.data.scanApp || '?'}`
})

// 短信发码：无效手机号应被上游业务拒绝（400 + 官方文案），而非 502 网关错
await test('sms 坏手机号拒绝 POST /login/qs/sms', async() => {
  const r = await postJson('/login/qs/sms', { mobile: '10000000000' })
  if (r.status !== 400 || !r.body?.msg) throw new Error(`期望 400+msg，实际 ${r.status} ${JSON.stringify(r.body)}`)
  return r.body.msg
})

// 短信登录：未发码直接提交，应被上游业务拒绝（400 + 官方文案）
await test('sms 错误验证码拒绝 POST /login/qs/sms/login', async() => {
  const r = await postJson('/login/qs/sms/login', { mobile: '10000000000', code: '000000' })
  if (r.status !== 400 || !r.body?.msg) throw new Error(`期望 400+msg，实际 ${r.status} ${JSON.stringify(r.body)}`)
  return r.body.msg
})

// sessionid 手动绑定：裸 sessionid 与完整 cookie 串两种形态均可绑，登录态生效；
// 无上游校验接口，测完解绑恢复匿名（避免假 sessionid 影响后续用例）
await test('qs sessionid 绑定 POST /login/qs', async() => {
  const bare = await postJson('/login/qs', { sessionid: 'smoke_fake_sessionid_0123456789abcdef' })
  if (bare.status !== 200 || bare.body?.code !== 0 || bare.body.data?.logged !== true) throw new Error(`HTTP ${bare.status} ${JSON.stringify(bare.body)}`)
  const full = await postJson('/login/qs', { cookie: 'sid_guard=smoke; sessionid=smoke_fake_sessionid_0123456789abcdef' })
  if (full.status !== 200 || full.body?.code !== 0) throw new Error(`HTTP ${full.status} ${JSON.stringify(full.body)}`)
  const st = await get('/login/status')
  if (st.code != 0 || st.data?.qs?.logged !== true) throw new Error('qs 登录状态未生效')
  const del = await fetch(`${BASE}/login/qs`, { method: 'DELETE' })
  if (del.status !== 200) throw new Error(`解绑失败 HTTP ${del.status}`)
  const st2 = await get('/login/status')
  if (st2.data?.qs?.logged !== false) throw new Error('解绑后状态未恢复')
  return 'bare+cookie 两种形态可绑，已解绑'
})

// sessionid 绑定校验（缺 sessionid 字段拒绝）
await test('qs 坏 cookie 拒绝 POST /login/qs', async() => {
  const r = await postJson('/login/qs', { cookie: 'foo=bar; baz=1' })
  if (r.status !== 422) throw new Error(`期望 422，实际 ${r.status}`)
  return `status ${r.status}`
})

// ---------- AUTH_KEY 鉴权与流签名（独立实例） ----------

// 独立实例也取随机空闲端口（主实例 PORT=0 随机模式下不能用 PORT+1）
const freePorts: number[] = []
for (let i = 0; i < 2; i++) {
  freePorts.push(await new Promise<number>(resolve => {
    const srv = net.createServer()
    srv.listen(0, '127.0.0.1', () => {
      const p = (srv.address() as any).port
      srv.close(() => resolve(p))
    })
  }))
}

const AUTH_TEST_KEY = 'smoke_auth_key'
const authPort = freePorts[0]
const authBase = `http://localhost:${authPort}`
const authServer: any = await startLocalServer(authPort, { RATE_LIMIT: '0', AUTH_KEY: AUTH_TEST_KEY })

const authFetch = (p: string, opts: any = {}) => fetch(`${authBase}${p}`, opts)
const keyHeaders = { 'X-Server-Key': AUTH_TEST_KEY }

await test('AUTH_KEY 无秘钥 401', async() => {
  const r = await authFetch('/sources')
  if (r.status !== 401) throw new Error(`期望 401（只认 X-Server-Key），实际 ${r.status}`)
  return 'ok'
})
await test('AUTH_KEY X-Server-Key 通过', async() => {
  const r = await authFetch('/sources', { headers: keyHeaders })
  if (r.status !== 200) throw new Error(`期望 200，实际 ${r.status}`)
  return 'ok'
})
// 防扫描：全部页面（含文档/健康检查/不存在的路径）无秘钥一律 401，无法探测接口存在性。
// 断言必须包在 test() 内 —— 裸抛错会中断整个脚本，跳过后续用例与实例回收（留下孤儿进程）
await test('AUTH_KEY 全路径防扫（文档/health/404 均 401）', async() => {
  for (const p of ['/health', '/docs', '/openapi.json', '/redoc', '/not-exist-path', '/login/status']) {
    const r = await authFetch(p)
    if (r.status !== 401) throw new Error(`${p} 期望 401，实际 ${r.status}（应全路径要求秘钥）`)
  }
  return '6 条路径均 401'
})
// /stream/* 流代理：支持 X-Server-Key 头，或短时签名 ?exp=&sig=（不落裸秘钥到 URL）；裸 ?key= 已废弃
await test('AUTH_KEY 流代理签名机制', async() => {
  // 裸秘钥 ?key= 已不生效
  const rawKey = await authFetch(`/stream/qs/123?key=${AUTH_TEST_KEY}`)
  if (rawKey.status !== 401) throw new Error(`裸 ?key= 应 401，实际 ${rawKey.status}`)
  // X-Server-Key 头可通过鉴权（后续业务可能 502，但不能是 401）
  const withHeader = await authFetch('/stream/qs/123', { headers: keyHeaders })
  if (withHeader.status === 401) throw new Error('X-Server-Key 应通过流代理鉴权')
  // 正确签名通过；错签名 401
  const exp = Math.floor(Date.now() / 1000) + 600
  const crypto = await import('node:crypto')
  const sig = crypto.createHmac('sha256', AUTH_TEST_KEY).update(`/stream/qs/123:${exp}`).digest('hex').slice(0, 32)
  const signed = await authFetch(`/stream/qs/123?exp=${exp}&sig=${sig}`)
  if (signed.status === 401) throw new Error('有效签名应通过流代理鉴权')
  const badSig = await authFetch(`/stream/qs/123?exp=${exp}&sig=${'0'.repeat(32)}`)
  if (badSig.status !== 401) throw new Error(`错签名应 401，实际 ${badSig.status}`)
  // 过期签名 401
  const oldExp = Math.floor(Date.now() / 1000) - 10
  const oldSig = crypto.createHmac('sha256', AUTH_TEST_KEY).update(`/stream/qs/123:${oldExp}`).digest('hex').slice(0, 32)
  const expired = await authFetch(`/stream/qs/123?exp=${oldExp}&sig=${oldSig}`)
  if (expired.status !== 401) throw new Error(`过期签名应 401，实际 ${expired.status}`)
  return '头/签名通过，裸key/错签/过期签均 401'
})

// 限流（独立低阈值实例验证 429）
const rlPort = freePorts[1]
const rlBase = `http://localhost:${rlPort}`
const rlServer: any = await startLocalServer(rlPort, { AUTH_KEY: AUTH_TEST_KEY, RATE_LIMIT: '3' })
await test('RATE_LIMIT 超限 → 429', async() => {
  const statuses: number[] = []
  for (let i = 0; i < 6; i++) {
    const r = await fetch(`${rlBase}/sources`, { headers: { 'X-Server-Key': AUTH_TEST_KEY } })
    statuses.push(r.status)
  }
  if (!statuses.some(s => s === 429)) throw new Error(`6 连发应触发 429，实际 ${statuses.join(',')}`)
  return `状态序列 ${statuses.join(',')}`
})

rlServer.kill()
authServer.kill()


// 汇总
const passed = results.filter(r => r.ok).length
const failed = results.filter(r => !r.ok)
console.log(`\n===== 结果: ${passed}/${results.length} 通过 =====`)
if (failed.length) {
  console.log('失败项:')
  failed.forEach(f => console.log(`  - ${f.name}: ${f.detail}`))
}

if (localServer) {
  console.log('\n正在关闭本地服务器...')
  localServer.kill()
  await new Promise(resolve => { localServer.on('exit', resolve) })
}

if (failed.length) process.exit(1)
