// 汽水音乐 URL 获取（直连）
// 两条取流通道：
//   匿名（未登录）: seo_track → url_player_info（VOD GetPlayInfo 免登录）→ 未加密试听直链（30s 132k）
//   登录态:         官方 PC 签名 X-Helios/X-Medusa（见 qs/mssdk.ts）→ /luna/pc/track_v2
//                   → 加密音频（cenc-aes-ctr）→ spade key 解密 → /stream/qs/:id 代理端点回明文流
//                   （相对地址，路由层负责补全 origin）
//   实测（2026-10-01）: track_v2 匿名=加密试听；完整曲目需登录 sessionid（汽水音乐 APP 扫码 /login/qs/qr）。
//   登录请求失败自动降级匿名试听。
// 统一接口约定：getMusicUrl(songInfo, type) → { promise, cancelHttp }
// promise resolve → { type, url }

import { httpFetch } from './lib/http.ts'
import { createHash, randomBytes } from 'node:crypto'
import musicInfo from './musicInfo.ts'
import { getCookie, getSessionId } from './passport.ts'
import { mssdkAvailable, signQishuiRequest, pcCommonParams, PC_UA } from './mssdk.ts'
import { decryptAudioBuffer, resolveAudioKey } from './audiodecrypt.ts'
import type { HttpResponse } from './lib/types.ts'

// 官方直连接口支持的音质：授权曲匿名只有 30 秒试听 128k（登录态取码率最高条目，324k/hi_res 视权益）；
// 用户上传曲匿名即可整曲（实测 2026-10-02：DJ 版整曲 204.84s / 三档 68k・132k・260k）
export const supportQualitys = ['128k', '320k']

const TRACK_V2_URL = 'https://api.qishui.com/luna/pc/track_v2'
const STREAM_PREFIX = '/stream/qs/'
const STREAM_CACHE_TTL = 10 * 60_000
const STREAM_CACHE_MAX = 3

interface StreamEntry {
  buffer: Buffer
  contentType: string
  bitrate: number
  size: number
  resolvedAt: number
}

// 已解密音频缓存（/url 解析一次，/stream 直接消费；LRU + TTL）
const streamCache = new Map<string, StreamEntry>()
const inflight = new Map<string, Promise<StreamEntry>>()

const cachePut = (trackId: string, entry: StreamEntry) => {
  streamCache.delete(trackId) // 重新插入以刷新 LRU 顺序
  streamCache.set(trackId, entry)
  while (streamCache.size > STREAM_CACHE_MAX) {
    streamCache.delete(streamCache.keys().next().value!)
  }
}

/** 已缓存的明文音频（/stream/qs/:id 用；缓存未命中返回 null，由路由触发重解析） */
export const getCachedStream = (trackId: string): StreamEntry | null => {
  const entry = streamCache.get(trackId)
  if (!entry) return null
  if (Date.now() - entry.resolvedAt > STREAM_CACHE_TTL) {
    streamCache.delete(trackId)
    return null
  }
  return entry
}

// 匿名通道：码率最高的未加密条目（带 PlayAuth = 加密，直接回 URL 不可播，跳过）
const pickPlainAudioUrl = (playInfoList: any): string | null => {
  const entries = (playInfoList || []).filter(item => item?.MainPlayUrl && !item.PlayAuth)
  if (!entries.length) return null
  entries.sort((a, b) => (b.Bitrate || 0) - (a.Bitrate || 0))
  return entries[0].MainPlayUrl
}

const anonymousPreviewUrl = async (trackId: string): Promise<string> => {
  const body = await musicInfo(trackId).promise
  const playerInfoUrl = body?.track_player?.url_player_info
  if (!playerInfoUrl) throw new Error('获取失败')
  const { statusCode, body: playInfo } = await httpFetch(playerInfoUrl, { method: 'get', timeout: 15000 }).promise
  const url = statusCode == 200 ? pickPlainAudioUrl(playInfo?.Result?.Data?.PlayInfoList) : null
  if (!url) throw new Error('获取失败')
  return url
}

// 登录态通道：官方 PC 客户端签名 X-Helios/X-Medusa（签名服务器或固定值，见 qs/mssdk.ts）
const pickBestEntry = (playInfoList: any) => {
  const entries = (playInfoList || []).filter(item => item?.MainPlayUrl)
  if (!entries.length) return null
  entries.sort((a, b) => (b.Bitrate || 0) - (a.Bitrate || 0))
  return entries[0]
}

const fetchTrackPlayer = async (url: string, headers: Record<string, string>, requestBody: string) => {
  const { statusCode, body } = (await httpFetch(url, {
    method: 'post',
    headers,
    body: requestBody,
    timeout: 15000,
  }).promise) as HttpResponse
  const trackPlayer = (body as any)?.track_player
  if (statusCode != 200 || !trackPlayer?.url_player_info) {
    const errMsg = (body as any)?.status_info?.status_msg || (body as any)?.message || ''
    throw new Error(`track_v2 获取失败: HTTP ${statusCode} ${errMsg}`.trim())
  }
  return trackPlayer as { url_player_info: string }
}

const TRACK_V2_BODY = (trackId: string) =>
  JSON.stringify({ track_id: trackId, media_type: 'track', queue_type: 'search_one_track', scene_name: 'search' })

const fetchTrackPlayerByPc = async (trackId: string) => {
  const sessionId = getSessionId()
  if (!mssdkAvailable()) throw new Error('PC 签名不可用（16689 未探测到签名服务，可配置 QS_SIGN_SERVER 或 data/qs-sign.json，见 qs-signer 仓库）')
  const url = `${TRACK_V2_URL}?${new URLSearchParams(pcCommonParams()).toString()}`
  const requestBody = TRACK_V2_BODY(trackId)
  const headers: Record<string, string> = {
    'content-type': 'application/json; charset=utf-8',
    'user-agent': PC_UA,
    'x-luna-background-type': 'foreground',
    'x-luna-is-background-req': '0',
    'x-luna-is-local-user': '0',
    'x-tt-trace-id': `00-${randomBytes(8).toString('hex')}-${randomBytes(8).toString('hex')}-01`,
    'x-ss-stub': createHash('md5').update(requestBody).digest('hex').toUpperCase(),
    ...(sessionId ? { cookie: getCookie() || `sessionid=${sessionId}` } : {}),
  }
  const signed = await signQishuiRequest(url, headers, requestBody)
  if (!signed) throw new Error('官方签名失败')
  return fetchTrackPlayer(url, { ...headers, ...signed }, requestBody)
}

const resolveSignedStream = async (trackId: string): Promise<StreamEntry> => {
  const trackPlayer = await fetchTrackPlayerByPc(trackId)

  const playInfoRes = await httpFetch(trackPlayer.url_player_info, { method: 'get', timeout: 15000 }).promise
  const entry = pickBestEntry((playInfoRes.body as any)?.Result?.Data?.PlayInfoList)
  if (!entry) throw new Error('无可用音频资源')

  const audioRes = await httpFetch(entry.MainPlayUrl, { method: 'get', timeout: 30000 }).promise
  const encrypted = audioRes.raw
  if (!encrypted?.length) throw new Error('音频下载失败')

  let buffer: Buffer = encrypted
  let contentType = 'audio/mp4'
  if (entry.PlayAuth) {
    const hexKey = resolveAudioKey({ spade_a: entry.PlayAuth })
    const decrypted = decryptAudioBuffer(new Uint8Array(encrypted), hexKey)
    buffer = Buffer.from(decrypted.buffer)
    contentType = decrypted.content_type
  }

  const streamEntry: StreamEntry = {
    buffer,
    contentType,
    bitrate: entry.Bitrate || 0,
    size: buffer.length,
    resolvedAt: Date.now(),
  }
  cachePut(trackId, streamEntry)
  return streamEntry
}

/** 解析明文音频流（/stream/qs/:id 用；先查缓存，未命中重新解析） */
export const getStreamBuffer = async (trackId: string): Promise<StreamEntry> => {
  const cached = getCachedStream(trackId)
  if (cached) return cached
  let work = inflight.get(trackId)
  if (!work) {
    work = resolveSignedStream(trackId).finally(() => inflight.delete(trackId))
    inflight.set(trackId, work)
  }
  return work
}

export const getMusicUrl = (songInfo, type) => {
  const requestObj = { cancelHttp() {}, promise: null as any }
  const trackId = String(songInfo.songmid ?? '')
  const sessionId = getSessionId()
  if (sessionId) {
    // 登录态：签名 track_v2 + 服务端解密，/url 阶段完成解析（快速失败），/stream 直接消费缓存
    requestObj.promise = getStreamBuffer(trackId)
      .then(entry => {
        console.log(`[qs/url] ${trackId} 解密流就绪 bitrate=${entry.bitrate} size=${entry.size}`)
        return { type, url: `${STREAM_PREFIX}${trackId}` }
      })
      .catch(err => {
        console.log(`[qs/url] ${trackId} 登录态取流失败，降级匿名试听: ${err.message}`)
        return anonymousPreviewUrl(trackId).then(url => ({ type, url }))
      })
    return requestObj
  }
  // 匿名：未加密试听直链
  requestObj.promise = anonymousPreviewUrl(trackId).then(url => ({ type, url }))
  return requestObj
}

export default { getMusicUrl, getStreamBuffer, getCachedStream }
