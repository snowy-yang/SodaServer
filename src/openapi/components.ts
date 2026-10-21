// OpenAPI 领域模型 —— 注册为 Fastify 共享 schema（fastify.addSchema）
// 路由的 response schema 通过 `名称#` 形式的 $ref 引用；
// @fastify/swagger 生成文档时会自动映射到 components/schemas/名称
// 注：additionalProperties: true 保证运行时序列化不剥离未声明字段

const ref = (name) => ({ $ref: `${name}#` })

export const COMPONENT_SCHEMAS = {
  ErrorEnvelope: {
    type: 'object',
    properties: {
      code: { type: 'integer', description: '与 HTTP 状态码一致', example: 422 },
      msg: { type: 'string', description: '错误描述' },
    },
    additionalProperties: true,
    required: ['code', 'msg'],
  },
  QualityInfo: {
    type: 'object',
    properties: { size: { type: 'string', example: '9.8M' } },
    additionalProperties: true,
  },
  SongType: {
    type: 'object',
    properties: {
      type: { type: 'string', example: '320k', enum: ['128k', '320k'] },
      size: { type: 'string', example: '9.8M' },
    },
    additionalProperties: true,
  },
  SongInfo: {
    type: 'object',
    properties: {
      songmid: { type: 'string', description: '汽水 track id' },
      albumId: { type: 'string' },
      name: { type: 'string', description: '歌名' },
      singer: { type: 'string', description: '歌手' },
      source: { type: 'string', description: '来源平台 id（恒为 qs）' },
      interval: { type: 'string', description: '时长，格式 mm:ss' },
      albumName: { type: 'string' },
      img: { type: ['string', 'null'], description: '封面地址' },
      types: { type: 'array', items: ref('SongType'), description: '可用音质列表' },
      _types: { type: 'object', additionalProperties: ref('QualityInfo') },
      typeUrl: { type: 'object' },
    },
    additionalProperties: true,
  },
  SearchResult: {
    type: 'object',
    properties: {
      source: { type: 'string', description: '来源平台 id（恒为 qs）' },
      list: { type: 'array', items: ref('SongInfo') },
      page: { type: 'integer' },
      limit: { type: 'integer' },
    },
    additionalProperties: true,
  },
  MusicUrlResult: {
    type: 'object',
    properties: {
      type: { type: 'string', example: '128k', description: '实际命中的音质（登录态为解密完整曲目，匿名只有 30 秒试听）' },
      url: { type: 'string', description: '播放地址；登录态为 /stream/qs/:id 代理地址（已补全 origin 并附签名）' },
    },
    additionalProperties: true,
  },
  LyricInfo: {
    type: 'object',
    properties: {
      lyric: { type: 'string', description: '原文歌词（LRC）' },
      tlyric: { type: 'string', description: '翻译歌词（汽水无翻译，恒为空串）' },
      yrc: { type: 'string', description: '逐字歌词' },
    },
    additionalProperties: true,
    required: ['lyric', 'tlyric'],
  },
  PicResult: {
    type: 'object',
    properties: { url: { type: 'string', description: '封面地址' } },
    additionalProperties: true,
  },
}
