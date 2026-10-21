// 网络请求模块（自 MusicServer sdk/common/request.ts 移植，requestMsg 内联）
// 接口保持一致：
//   httpFetch(url, { method, headers, body, form, timeout, lookup, family })
//     → { promise, cancelHttp }
//   promise resolve → { statusCode, body, headers, raw }
//     - body：JSON 已解析对象，或原始字符串（HTML/文本）
//     - raw：响应体 Buffer
//   promise reject → 错误（timeout 请求超时 / canceled 主动取消）
// 注意：promise 与 cancelHttp 均为普通可赋值属性（调用方会在 .then 链中改写 promise）

import http from 'node:http'
import https from 'node:https'
import zlib from 'node:zlib'
import { URL } from 'node:url'

const MAX_BUFFER_SIZE = 64 * 1024 * 1024 // 64MB

export interface HttpOptions {
  method?: string
  headers?: Record<string, any>
  body?: any
  form?: Record<string, any>
  timeout?: number
  lookup?: any
  family?: number
  [key: string]: any
}

export interface HttpFetchResponse {
  statusCode: number
  statusMessage?: string
  headers: Record<string, any>
  body: any
  raw: Buffer
}

export interface HttpRequestObject {
  promise: Promise<HttpFetchResponse>
  cancelHttp(): void
  isCancelled: boolean
  requestObj?: any
  cancelFn?: ((err: any) => void) | null
  [key: string]: any
}

const defaultHeaders = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.100 Safari/537.36',
}

const parseBody = (raw: Buffer): any => {
  if (!raw || !raw.length) return null
  const text = raw.toString('utf8')
  // 与原版一致：无论 content-type 都尝试 JSON 解析，失败则返回原始文本
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/**
 * 发起请求（返回带 promise 与 cancelHttp 的对象）
 */
const buildHttpPromise = (url: string, options: HttpOptions = {}): HttpRequestObject => {
  const method = (options.method || 'GET').toUpperCase()
  const obj: HttpRequestObject = {
    promise: null as any,
    isCancelled: false,
    cancelHttp: () => {
      if (!obj.requestObj) {
        obj.isCancelled = true
        return
      }
      obj.requestObj.destroy(new Error('Canceled'))
      obj.requestObj = null
      // 取消后置空是运行时事实，但公开类型保持非空（消费方总是先取 promise 再取消）
      ;(obj as any).promise = (obj as any).cancelHttp = null
      obj.cancelFn?.(new Error('Canceled'))
      obj.cancelFn = null
    },
  }
  obj.promise = new Promise((resolve, reject) => {
    obj.cancelFn = reject

    let parsedUrl: URL
    try {
      parsedUrl = new URL(url)
    } catch (err) {
      reject(err)
      return
    }

    const isHttps = parsedUrl.protocol === 'https:'
    const client = isHttps ? https : http

    // 数据体构造：body（对象/字符串/Buffer）与 form（对象 → urlencoded）
    let body: string | any = null
    let isForm = false
    if (options.form) {
      const formBody = new URLSearchParams()
      for (const key of Object.keys(options.form)) {
        const value = options.form[key]
        if (value == null) continue
        formBody.append(key, typeof value === 'object' ? JSON.stringify(value) : value)
      }
      body = formBody.toString()
      isForm = true
    } else if (options.body) {
      body = typeof options.body === 'object' && !Buffer.isBuffer(options.body) ? JSON.stringify(options.body) : options.body
    }

    const headers: Record<string, any> = { ...defaultHeaders, ...(options.headers || {}) }
    if (body && !headers['content-type'] && !headers['Content-Type']) {
      headers['content-type'] = isForm ? 'application/x-www-form-urlencoded' : 'application/json'
    }
    if (body) headers['content-length'] = Buffer.byteLength(body)

    const requestOptions: Record<string, any> = {
      method,
      headers,
      family: options.family || undefined,
    }
    if (options.lookup) requestOptions.lookup = options.lookup

    const req = client.request(parsedUrl, requestOptions, res => {
      obj.requestObj = null
      obj.cancelFn = null
      const statusCode = res.statusCode ?? 0

      let stream: any = res
      switch (res.headers['content-encoding']) {
        case 'gzip': stream = res.pipe(zlib.createGunzip()); break
        case 'deflate': stream = res.pipe(zlib.createInflate()); break
        case 'br': stream = res.pipe(zlib.createBrotliDecompress()); break
      }

      const chunks: Buffer[] = []
      let size = 0
      stream.on('data', chunk => {
        size += chunk.length
        if (size > MAX_BUFFER_SIZE) {
          stream.destroy(new Error('Response too large'))
          return
        }
        chunks.push(chunk)
      })
      stream.on('error', err => reject(err))
      stream.on('end', () => {
        const raw = Buffer.concat(chunks)
        resolve({
          statusCode,
          headers: res.headers,
          body: parseBody(raw),
          raw,
        })
      })
    })

    obj.requestObj = req

    req.on('error', err => {
      const e = err as any
      if (obj.isCancelled || e.canceled) e.canceled = true
      reject(e)
    })

    if (options.timeout) {
      req.setTimeout(options.timeout, () => {
        const err = new Error('Request timeout') as any
        err.timeout = true
        req.destroy(err)
      })
    }

    if (body) req.write(body)
    req.end()
  })
  return obj
}

/**
 * 请求超时/断网错误统一处理
 */
export const httpFetch = (url: string, options: HttpOptions = { method: 'get' }): HttpRequestObject => {
  const requestObj = buildHttpPromise(url, options)
  requestObj.promise = requestObj.promise.catch((err: any) => {
    if (err.canceled) return Promise.reject(err)
    switch (err.code) {
      case 'ETIMEDOUT':
      case 'ESOCKETTIMEDOUT':
        return Promise.reject(new Error('请求超时'))
      case 'ENOTFOUND':
        return Promise.reject(new Error('无法连接到服务器'))
      case 'ECONNRESET':
        return Promise.reject(new Error('请求失败'))
      default:
        return Promise.reject(err)
    }
  })
  return requestObj
}

export default { httpFetch }
