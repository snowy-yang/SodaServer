// 业务路由 —— Fastify 插件形式
// schema（querystring/params）负责参数校验并生成文档；schema.response 负责文档中的响应结构
// 处理函数返回普通对象 → 由服务器的 preSerialization 钩子包成 { code: 0, data }
// 抛出 ApiError → 由全局 setErrorHandler 转为对应 HTTP 状态码
// 路径保留 MusicServer 的 /qs 段（/search/qs/...、/url/qs/...），点歌端换 base_url 即可接入

import { upstream } from './http/api-error.ts'
import { signStream } from './http/stream-sign.ts'
import qs from '../qs/index.ts'
import musicInfo from '../qs/musicInfo.ts'
import { supportQualitys } from '../qs/stream.ts'

const QUALITIES = supportQualitys

/** 统一解包 SDK 风格返回值：{ promise, cancelHttp } 结构或裸 Promise 兼容 */
const unwrap = (result: any): any => (
  result && typeof result.promise !== 'undefined' && typeof result.cancelHttp === 'function'
    ? result.promise
    : result
)

const envelope = (dataRef?: string) => ({
  type: 'object',
  properties: {
    code: { type: 'integer', description: '0 表示成功', example: 0 },
    data: dataRef ? { $ref: `${dataRef}#` } : {},
  },
  required: ['code', 'data'],
})

/** 组装路由的 schema.response：200 信封 + 通用错误码文档 */
const responses = (dataRef: any, { notFoundDoc = true, upstreamDoc = true }: { notFoundDoc?: boolean; upstreamDoc?: boolean } = {}) => ({
  '200': envelope(dataRef),
  '422': { $ref: 'ErrorEnvelope#' },
  ...(notFoundDoc ? { '404': { $ref: 'ErrorEnvelope#' } } : {}),
  ...(upstreamDoc ? { '502': { $ref: 'ErrorEnvelope#' } } : {}),
})

// ---- 常用 JSON Schema 片段 ----

const obj = (properties: any, required: string[] = []) => ({ type: 'object', properties, required })
const params = (properties: any, required?: string[]) => obj(properties, required ?? Object.keys(properties))
const pPage = { type: 'integer', minimum: 1, description: '页码（省略时为第 1 页）', example: 2 }
const pLimit = { type: 'integer', default: 20, minimum: 1, maximum: 100, description: '每页条数' }
const pQuality = { type: 'string', enum: QUALITIES, default: '128k', description: '音质' }
const pId = { type: 'string', description: '汽水 track id', example: '7079108541549643812' }

/**
 * 注册全部业务路由
 * @param {import('fastify').FastifyInstance} fastify
 */
export const musicRoutes = async(fastify: any) => {
  // ---------- 搜索 ----------

  const searchHandler = async(request) => {
    const { keyword, page } = request.params
    const { limit } = request.query
    const pageNo = Number(page) || 1
    try {
      return { source: 'qs', list: await qs.musicSearch.search(keyword, pageNo), page: pageNo, limit }
    } catch (err: any) {
      throw upstream(err.message)
    }
  }

  const searchSchema = (summary: string, withPage: boolean) => ({
    summary,
    description: 'PC 通道每页固定 20 条（上游不接受 count，limit 参数不改变页大小，仅回显）。' +
      '搜索依赖 PC 签名（QS_SIGN_SERVER 或 data/qs-sign.json），签名不可用时返回 502。',
    tags: ['搜索'],
    params: params({
      keyword: { type: 'string', description: '搜索关键词（URL 编码的中文可直接使用）', example: '稻香' },
      ...(withPage ? { page: pPage } : {}),
    }, withPage ? undefined : ['keyword']),
    querystring: obj({ limit: pLimit }),
    response: responses('SearchResult', { notFoundDoc: false }),
  })

  fastify.get('/search/qs/:keyword', { schema: searchSchema('搜索歌曲（第 1 页）', false) }, searchHandler)
  fastify.get('/search/qs/:keyword/:page', { schema: searchSchema('搜索歌曲（指定页）', true) }, searchHandler)

  // ---------- 歌曲资源 ----------

  fastify.get('/url/qs/:id', {
    schema: {
      summary: '获取播放地址',
      description: 'id 为汽水 track id。匿名只有 30 秒试听直链（授权曲；用户上传曲可匿名整曲）；' +
        '登录态（/login/qs/*）走签名 track_v2 + 服务端解密，返回 /stream/qs/:id 代理地址' +
        '（响应前已按请求 origin 补全为绝对地址；启用 AUTH_KEY 时附带短时 exp/sig 签名，播放器拉流免带自定义头）。' +
        '登录态取流失败自动降级匿名试听。',
      tags: ['歌曲资源'],
      params: params({ id: pId }),
      querystring: obj({ quality: pQuality }),
      response: responses('MusicUrlResult', { notFoundDoc: false }),
    },
  }, async(request) => {
    const { id } = request.params
    const { quality } = request.query
    const songInfo = { songmid: id, _types: {} }
    try {
      const res = await unwrap(qs.getMusicUrl(songInfo, quality))
      if (!res?.url) throw new Error('获取播放地址失败')
      // 解密流代理地址返回相对路径，按请求的协议与 host 补全（兼容本机/局域网播放器）；
      // 启用 AUTH_KEY 时附带短时签名（播放器拉流无法携带自定义头，签名不暴露秘钥本身）
      let url = String(res.url)
      if (url.startsWith('/')) {
        const suffix = process.env.AUTH_KEY
          ? `${url.includes('?') ? '&' : '?'}${signStream(process.env.AUTH_KEY, url.split('?')[0])}`
          : ''
        url = `${request.protocol}://${request.headers.host}${url}${suffix}`
      }
      return { type: res.type || quality, url }
    } catch (err: any) {
      throw upstream(err.message)
    }
  })

  // 解密流代理：/url/qs 登录态返回的相对地址，本端点完成「签名 track_v2 → 密音频下载 →
  // spade key + AES-CTR 解密 → 回明文流」；raw 豁免 { code, data } 信封包装
  fastify.get('/stream/qs/:id', {
    config: { raw: true },
    schema: {
      summary: '解密流代理',
      description: '取流响应中返回的相对地址（/url/qs 登录态），服务端下载上游密流并在本地解密后返回明文音频；' +
        '不接受客户端自行构造。解密结果缓存 10 分钟，过期后客户端重新 /url/qs 取地址。',
      tags: ['歌曲资源'],
      params: params({ id: pId }),
    },
  }, async(request, reply) => {
    const { id } = request.params
    try {
      const entry = await qs.getStreamBuffer(String(id))
      reply.type(entry.contentType || 'audio/mp4')
      reply.header('Accept-Ranges', 'none')
      reply.header('Content-Length', entry.buffer.length)
      return reply.send(entry.buffer)
    } catch (err: any) {
      throw upstream(err.message)
    }
  })

  const lyricHandler = async(request) => {
    const { id } = request.params
    try {
      const result = await unwrap(qs.getLyric({ songmid: id }))
      return result || { lyric: '', tlyric: '' }
    } catch (err: any) {
      throw err.status ? err : upstream(err.message)
    }
  }

  fastify.get('/lyric/qs/:id', {
    schema: {
      summary: '获取歌词',
      description: '逐字歌词来源为 seo_track（匿名可用），返回 LRC 原文与 YRC 逐字时间轴。',
      tags: ['歌曲资源'],
      params: params({ id: pId }),
      response: responses('LyricInfo', { notFoundDoc: false }),
    },
  }, lyricHandler)

  fastify.get('/pic/qs/:id', {
    schema: {
      summary: '获取封面',
      tags: ['歌曲资源'],
      params: params({ id: pId }),
      response: responses('PicResult', { notFoundDoc: false }),
    },
  }, async(request) => {
    const { id } = request.params
    try {
      const url = await unwrap(qs.getPic({ songmid: id }))
      return { url }
    } catch (err: any) {
      throw err.status ? err : upstream(err.message)
    }
  })

  fastify.get('/info/qs/:id', {
    schema: {
      summary: '获取曲目元数据',
      description: 'seo_track 全量数据（匿名可用）：曲目（name/artists/album/duration/bit_rates）、逐字歌词原始内容、' +
        '试听播放信息（track_player）。/url /lyric /pic 均基于此通道。',
      tags: ['歌曲资源'],
      params: params({ id: pId }),
    },
  }, async(request) => {
    const { id } = request.params
    try {
      return await musicInfo(String(id)).promise
    } catch (err: any) {
      throw err.status ? err : upstream(err.message)
    }
  })

  // ---------- 系统 ----------

  fastify.get('/health', {
    schema: {
      summary: '健康检查',
      tags: ['系统'],
      response: responses(null, { notFoundDoc: false, upstreamDoc: false }),
    },
  }, async() => ({
    status: 'ok',
    uptime: Math.round(process.uptime()),
  }))

  fastify.get('/sources', {
    schema: {
      summary: '获取支持的平台',
      description: '单平台服务端，恒返回汽水一项；音质为官方直连接口的支持范围。',
      tags: ['系统'],
      response: responses(null, { notFoundDoc: false, upstreamDoc: false }),
    },
  }, async() => ({
    platforms: [{
      id: 'qs',
      name: '汽水音乐',
      qualities: QUALITIES,
      capabilities: {
        search: true,
        url: true,
        lyric: true,
        pic: true,
        boards: false,
        hotSearch: false,
        songList: false,
        singer: false,
        album: false,
        albumSearch: false,
        playlist: false,
        playlistSearch: false,
        playlistCategories: false,
        parse: false,
        userPlaylist: false,
      },
    }],
  }))
}
