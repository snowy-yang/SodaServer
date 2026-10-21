// 汽水音乐搜索（App 搜索接口直连）
// 接口: https://api.qishui.com/luna/search/track?<公共参数>&q=<关键词>&cursor=
// 官方 PC 通道: luna_pc 参数 + X-Helios/X-Medusa 签名（签名服务器或固定值，见 qs/mssdk.ts）。
//               PC 签名不可用时搜索直接报错；被风控时上游返回 200 + 空 body（无 result_groups）。
import { sizeFormate, formatPlayTime } from './lib/utils.ts'
import { httpFetch } from './lib/http.ts'
import { coverUrl } from './musicInfo.ts'
import { mssdkAvailable, signQishuiRequest, pcCommonParams, PC_UA } from './mssdk.ts'

const PC_PAGE_SIZE = 20

const buildSearchUrl = (str: string, cursor: number): string => {
  const params = new URLSearchParams({
    ...pcCommonParams(),
    q: str,
    cursor: String(cursor),
    search_id: '',
    search_method: 'input',
    debug_params: '',
    from_search_id: '',
    search_scene: 'normal',
    scene_name: 'search',
  })
  return `https://api.qishui.com/luna/search/track?${params.toString()}`
}

export default {
  limit: 30,
  total: 0,
  page: 0,
  allPage: 1,
  getSinger(artists) {
    return (artists || []).map(singer => singer.name).filter(Boolean).join('、')
  },
  handleResult(rawList) {
    if (!rawList) return []
    return rawList.map(item => {
      const track = item?.entity?.track
      if (!track?.id) return null
      // types 反映匿名实际可获取的资源：30 秒试听（preview.bit_rates 为试听码率）
      // track.bit_rates 是完整曲目码率元数据（66k~hi_res），但匿名无权取流，不对外声明
      const previewSize = track.preview?.bit_rates?.[0]?.size
      const size = previewSize ? sizeFormate(previewSize) : null
      const types = [{ type: '128k', size }]
      const _types = { '128k': { size } }
      return {
        singer: this.getSinger(track.artists),
        name: track.name,
        albumName: track.album?.name ?? null,
        albumId: track.album?.id ?? null,
        source: 'qs',
        interval: formatPlayTime(track.duration / 1000),
        songmid: String(track.id),
        img: coverUrl(track.album),
        lrc: null,
        types,
        _types,
        typeUrl: {},
      }
    }).filter(Boolean)
  },
  handleSearchBody(rawBody, cursor, page) {
    const group = rawBody.result_groups.find(g => g?.data?.length) || { data: [], has_more: false }
    const list = this.handleResult(group.data)
    const total = group.has_more ? cursor + list.length + 1 : cursor + list.length
    this.total = total
    this.page = page
    this.allPage = group.has_more ? page + 1 : page
    return {
      list,
      allPage: this.allPage,
      limit: this.limit,
      total,
      source: 'qs' as const,
    }
  },
  async search(str, page = 1) {
    if (!mssdkAvailable()) throw new Error('PC 签名不可用（16689 未探测到签名服务，可配置 QS_SIGN_SERVER 或 data/qs-sign.json，见 qs-signer 仓库）')
    // PC 通道每页固定 20 条（上游默认、不接受 count），cursor 必须按 20 步进，否则翻页与 limit 错位重叠
    const cursor = PC_PAGE_SIZE * (page - 1)
    const url = buildSearchUrl(str, cursor)
    // 签名覆盖真实发出的头：先定头、再签、再发
    const headers: Record<string, string> = { 'User-Agent': PC_UA }
    const signed = await signQishuiRequest(url, headers)
    if (!signed) throw new Error('官方签名失败')
    const { body } = await httpFetch(url, { method: 'get', headers: { ...headers, ...signed }, timeout: 15000 }).promise
    // 被风控/无结果时上游不返回 result_groups（200 + {"extra":{"empty_search":1}}）
    if (!body?.result_groups) throw new Error('搜索无结果（疑似风控）')
    return this.handleSearchBody(body, cursor, page)
  },
}
