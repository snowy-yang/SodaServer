// 汽水音乐歌词（逐字时间轴 → LRC/YRC）
// 来源: seo_track 的 lyric.content，行格式 `[起(ms),长(ms)]<行内偏移(ms),长(ms),0>字...`
// 与网易云 yrc 同族（wy 的 parseTools 处理 (a,b,c) 括号变体，这里是尖括号）
import musicInfo from './musicInfo.ts'

const lineRe = /^\[(\d+),\d+\]/
const wordRe = /<(\d+),(\d+),\d+>([^<]*)/g

const msFormat = (timeMs: number) => {
  if (Number.isNaN(timeMs)) return ''
  let ms = timeMs % 1000
  timeMs /= 1000
  const m = parseInt(String(timeMs / 60)).toString().padStart(2, '0')
  timeMs %= 60
  const s = parseInt(String(timeMs)).toString().padStart(2, '0')
  return `[${m}:${s}.${ms}]`
}

const parseTimedLyric = (content: string) => {
  const lrcLines: string[] = []
  const yrcLines: string[] = []
  for (const rawLine of String(content || '').split('\n')) {
    const line = rawLine.trim()
    const lineMatch = lineRe.exec(line)
    if (!lineMatch) continue
    const timeTag = msFormat(parseInt(lineMatch[1]))
    if (!timeTag) continue
    const words = [...line.slice(lineMatch[0].length).matchAll(wordRe)]
    if (!words.length) continue
    const text = words.map(word => word[3]).join('')
    if (!text.trim()) continue // 间奏空行
    lrcLines.push(`${timeTag}${text}`)
    yrcLines.push(`${timeTag}${words.map(word => `<${word[1]},${word[2]}>${word[3]}`).join('')}`)
  }
  return { lyric: lrcLines.join('\n'), yrc: yrcLines.join('\n') }
}

export default (trackId: string) => {
  const requestObj = musicInfo(trackId)
  requestObj.promise = requestObj.promise.then(body => {
    const info = parseTimedLyric(body?.lyric?.content)
    if (!info.lyric) return Promise.reject(new Error('获取歌词失败'))
    return { lyric: info.lyric, tlyric: '', yrc: info.yrc }
  }) as any
  return requestObj
}
