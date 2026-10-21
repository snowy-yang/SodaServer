// 汽水 PC 通道签名（X-Helios / X-Medusa）—— 三种供给方式，按优先级：
//   1) 签名服务器（显式）：环境变量 QS_SIGN_SERVER 指向签名服务（如 http://127.0.0.1:16889，
//      可选 QS_SIGN_TOKEN 配 Bearer 鉴权），HTTP POST /sign。
//      契约兼容独立仓库 qs-signer（gitea.4i.hk/hhhge/qs-signer，与 SodaServer 同级）
//      的 mssdk-sign 服务（请求 {url, headers, body?}，响应 {ok, headers, added?}）。
//   2) 签名服务器（自动探测）：QS_SIGN_SERVER 未配置时，进程启动探测本机 16689 端口——
//      GET /healthz 应答 ok:true 且 service:"mssdk-sign" 才认定为汽水签名服务并自动接入
//      （16689 上是别的服务则忽略，端口没人听静默跳过，均回落到 3）。
//   3) 固定值兜底：无签名服务器时，两个加密头直接用抓包固定值 ——
//      环境变量 QS_HELIOS / QS_MEDUSA，或 data/qs-sign.json：
//        { "helios": "…", "medusa": "…", "device_id": "…", "install_id": "…" }
//      签名与设备/URL 绑定：固定值必须搭配抓包时的同一套 device_id/install_id
//      （json 里带上即自动对齐 URL 参数），时效自负。
//   两者都没有：PC 签名不可用 —— 搜索/取流走 track_v2 的通道直接报错（匿名试听不受影响）。
// 官方只对 qishui.com 且 **非** /passport/、非 /ttwid/ 的请求签名 —— 登录链路用不到本模块。
// 签名覆盖 URL + 全部请求头；POST 的 x-ss-stub（body 的 MD5 大写）既是签名输入也要随请求发出。
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { getDeviceIds } from './passport.ts'

// 仓库根（本文件在 qs/ 下）——不依赖 process.cwd()，从任何目录启动都能找到
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const FIXED_SIGN_FILE = join(REPO_ROOT, 'data', 'qs-sign.json')
const SIGN_TIMEOUT = 5000

// —— 端口探测：QS_SIGN_SERVER 未配置时，启动探测本机 16689 是不是汽水签名服务 ——
const PROBE_BASE = 'http://127.0.0.1:16689'
const PROBE_TIMEOUT = 2000

// undefined=探测未完成；null=探测过、不是签名服务；字符串=探测到的签名服务地址
let probedServer: string | null | undefined
let probing: Promise<void> | null = null

/** healthz 应答 ok:true 且 service:"mssdk-sign" 才认——16689 上可能是无关服务 */
const isSignService = (json: any): boolean => json?.ok === true && json?.service === 'mssdk-sign'

const probeOnce = async (): Promise<void> => {
  try {
    const res = await fetch(`${PROBE_BASE}/healthz`, { signal: AbortSignal.timeout(PROBE_TIMEOUT) })
    const json = await res.json().catch(() => null) as any
    if (res.ok && isSignService(json)) {
      probedServer = PROBE_BASE
      console.log(`[qs/mssdk] 探测到本机 16689 是汽水签名服务（mssdk-sign v${json.version ?? '?'}，dll ${json.dllVersion ?? '?'}），自动接入`)
    } else {
      probedServer = null
      console.log(`[qs/mssdk] 本机 16689 有服务但不是汽水签名服务（healthz service=${json?.service ?? '非 JSON'}），忽略`)
    }
  } catch {
    probedServer = null // 端口没人监听是常态，静默
  }
}

/** 确保探测完成（进程内只探一次）；显式配置了 QS_SIGN_SERVER 则永不探测 */
const ensureProbe = async (): Promise<void> => {
  if (probedServer !== undefined || process.env.QS_SIGN_SERVER?.trim()) return
  probing ??= probeOnce()
  await probing
}

// 启动即探测一次（异步进行，不阻塞导入）；探测期间进来的签名调用会等它出结果
void ensureProbe()

/** 固定值配置（helios/medusa 必填；device_id/install_id 应与抓包时同一套） */
interface FixedSignConfig {
  helios: string
  medusa: string
  deviceId: string
  installId: string
}

let fixedConfigWarned = false

/** 读固定值：环境变量 QS_HELIOS/QS_MEDUSA 优先，其次 data/qs-sign.json；没有返回 null */
const readFixedConfig = (): FixedSignConfig | null => {
  const envHelios = process.env.QS_HELIOS?.trim()
  const envMedusa = process.env.QS_MEDUSA?.trim()
  if (envHelios && envMedusa) {
    return {
      helios: envHelios,
      medusa: envMedusa,
      deviceId: process.env.QS_SIGN_DEVICE_ID?.trim() ?? '',
      installId: process.env.QS_SIGN_INSTALL_ID?.trim() ?? '',
    }
  }
  if (!existsSync(FIXED_SIGN_FILE)) return null
  try {
    const data = JSON.parse(readFileSync(FIXED_SIGN_FILE, 'utf8')) as Record<string, unknown>
    const helios = String(data.helios ?? '').trim()
    const medusa = String(data.medusa ?? '').trim()
    if (helios && medusa) {
      return {
        helios,
        medusa,
        deviceId: String(data.device_id ?? '').trim(),
        installId: String(data.install_id ?? '').trim(),
      }
    }
    if (!fixedConfigWarned) {
      fixedConfigWarned = true
      console.log(`[qs/mssdk] ${FIXED_SIGN_FILE} 缺少 helios/medusa 字段，固定值模式不启用`)
    }
  } catch (err: any) {
    if (!fixedConfigWarned) {
      fixedConfigWarned = true
      console.log(`[qs/mssdk] 固定值配置解析失败: ${err?.message ?? err}`)
    }
  }
  return null
}

type SignMode =
  | { kind: 'server'; server: string; token: string }
  | { kind: 'fixed'; fixed: FixedSignConfig }

/** 当前签名供给方式：显式服务器 > 探测接入的服务器 > 固定值 > 不可用（null） */
const loadSignMode = (): SignMode | null => {
  const server = process.env.QS_SIGN_SERVER?.trim().replace(/\/+$/, '')
  if (server) return { kind: 'server', server, token: process.env.QS_SIGN_TOKEN?.trim() ?? '' }
  if (probedServer) {
    // 探测接入；签名服务开了鉴权时同样吃 QS_SIGN_TOKEN
    return { kind: 'server', server: probedServer, token: process.env.QS_SIGN_TOKEN?.trim() ?? '' }
  }
  const fixed = readFixedConfig()
  return fixed ? { kind: 'fixed', fixed } : null
}

/** PC 签名是否可用（只看配置与探测结果；服务器宕机等运行期失败由签名调用返回 null、调用方降级处理） */
export const mssdkAvailable = (): boolean =>
  // 探测未完成时先放行——紧随其后的 signQishuiRequest 会等探测结果再定供给
  Boolean(loadSignMode()) || probedServer === undefined

const signViaServer = async (
  server: string,
  token: string,
  url: string,
  headers: Record<string, string>,
  body?: string,
): Promise<Record<string, string> | null> => {
  try {
    const res = await fetch(`${server}/sign`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ url, headers, body }),
      signal: AbortSignal.timeout(SIGN_TIMEOUT),
    })
    const json = await res.json().catch(() => null) as any
    if (!res.ok || !json?.ok) {
      console.log(`[qs/mssdk] 签名服务返回异常: HTTP ${res.status} ${json?.error ?? ''}`.trim())
      return null
    }
    // signer/server.mjs 把 x-ss-stub 并进 headers，mssdk-sign 放 added —— 合并即可
    const signed: Record<string, string> = { ...(json.headers ?? {}), ...(json.added ?? {}) }
    if (!signed['X-Helios'] && !signed['x-helios']) {
      console.log('[qs/mssdk] 签名结果缺少 X-Helios')
      return null
    }
    return signed
  } catch (err: any) {
    console.log(`[qs/mssdk] 签名服务不可达: ${err?.message ?? err}`)
    return null
  }
}

/**
 * 给一个 qishui.com 请求签名
 * @param headers 必须是「真实要发出去的那一份」（含 cookie / x-ss-stub）
 * @param body    POST body（给了且 headers 没带 x-ss-stub 时服务端会自动补）
 * @returns 需追加的头；不可用时返回 null（调用方自行降级处理）
 */
export const signQishuiRequest = async (
  url: string,
  headers: Record<string, string>,
  body?: string,
): Promise<Record<string, string> | null> => {
  await ensureProbe() // 探测未完成时先等它出结果，再决定供给方式
  const mode = loadSignMode()
  if (!mode) return null
  if (mode.kind === 'fixed') return { 'X-Helios': mode.fixed.helios, 'X-Medusa': mode.fixed.medusa }
  return signViaServer(mode.server, mode.token, url, headers, body)
}

/** 官方 PC 端 UA（签名与 UA 绑定同一客户端版本） */
export const PC_UA = 'LunaPC/3.7.0(452316191)'

/** 官方 PC 端公共查询参数（与签名设备成套；track_v2 / search 等 luna_pc 接口共用） */
export const pcCommonParams = (): Record<string, string> => {
  const mode = loadSignMode()
  // 固定值是针对抓包设备签的，必须用配套设备；服务器现签则用本地设备（URL 带什么签什么）
  const fixedDevice = mode?.kind === 'fixed' && mode.fixed.deviceId ? mode.fixed : null
  const { deviceId, installId } = fixedDevice
    ? { deviceId: fixedDevice.deviceId, installId: fixedDevice.installId }
    : getDeviceIds()
  return {
    aid: '386088',
    app_name: 'luna_pc',
    region: 'cn',
    geo_region: 'cn',
    os_region: 'cn',
    sim_region: '',
    device_id: deviceId,
    cdid: '',
    iid: installId,
    version_name: '3.7.0',
    version_code: '30070000',
    channel: 'official',
    build_mode: 'master',
    network_carrier: '',
    ac: 'wifi',
    tz_name: 'Asia/Shanghai',
    resolution: '',
    device_platform: 'windows',
    device_type: 'Windows',
    os_version: 'Windows 11',
    fp: deviceId,
  }
}

export default { mssdkAvailable, signQishuiRequest, pcCommonParams, PC_UA }
