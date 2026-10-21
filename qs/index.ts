// 汽水音乐（字节跳动）平台模块
// 匿名可用面（2026-10-01 实测）：曲目信息 / 歌词 / 封面 / 30 秒试听取流（未加密直链）。
// 搜索与登录态取流需 PC 签名（QS_SIGN_SERVER 或固定值，见 qs/mssdk.ts 与 qs-signer 仓库）：
//   登录态（/login/qs/qr 扫码）走 X-Helios/X-Medusa 签名 /luna/pc/track_v2，
//   加密音频服务端解密后经 /stream/qs/:id 代理回明文流（完整曲目，2026-10-01 全链路实测）
import qsApi, { getStreamBuffer } from './stream.ts'
import getLyric from './lyric.ts'
import getMusicInfo, { coverUrl } from './musicInfo.ts'
import musicSearch from './musicSearch.ts'

const qs = {
  musicSearch,
  getMusicUrl(songInfo, type) {
    return qsApi.getMusicUrl(songInfo, type)
  },
  // 明文音频 Buffer（/stream/qs/:id 代理端点消费）
  getStreamBuffer(trackId: string) {
    return getStreamBuffer(trackId)
  },
  getLyric(songInfo) {
    return getLyric(songInfo.songmid)
  },
  getPic(songInfo) {
    return getMusicInfo(songInfo.songmid).promise.then(body => coverUrl(body?.seo_track?.track?.album))
  },
  getMusicDetailPageUrl(songInfo) {
    return `https://music.douyin.com/qishui/share/track?track_id=${songInfo.songmid}`
  },
}

export default qs
