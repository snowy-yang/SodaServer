// 汽水音乐曲目信息（H5 SEO 通道）
// 接口: https://beta-luna.douyin.com/luna/h5/seo_track?track_id=<id>&device_platform=web
// 匿名可访问（无签名要求）：返回曲目元数据（name/artists/album/duration/bit_rates/preview）、
// 逐字歌词（lyric.content）与试听播放信息（track_player.url_player_info / video_model）
// 注意：seo_track 属分享页/SEO 数据面，track_player 里只有 30 秒试听资源；
// 完整音频需真机凭证走 /luna/pc/track_v2，且音频经 CENC 加密，见 stream.ts 头注释
import { httpFetch } from './lib/http.ts'
import type { RequestObj } from './lib/types.ts'

export default (trackId: string): RequestObj<any> => {
  const requestObj = httpFetch(`https://beta-luna.douyin.com/luna/h5/seo_track?track_id=${trackId}&device_platform=web`, {
    method: 'get',
    headers: {
      'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36',
      'X-Requested-With': 'XMLHttpRequest',
    },
    timeout: 15000,
  })
  requestObj.promise = requestObj.promise.then(({ statusCode, body }) => {
    if (statusCode != 200 || !body?.seo_track?.track) return Promise.reject(new Error('获取歌曲信息失败'))
    return body
  })
  return requestObj
}
// 专辑封面 URL：urls[0] 为 CDN 前缀，uri 为资源路径，拼尺寸模板得方形封面（社区通用做法）
export const coverUrl = (album: any): string | null => {
  const urlCover = album?.url_cover
  if (!urlCover?.uri) return null
  const prefix = Array.isArray(urlCover.urls) && urlCover.urls.length ? urlCover.urls[0] : 'https://p3-luna.douyinpic.com/img/'
  return `${prefix}${urlCover.uri}~c5_500x500.jpg`
}
