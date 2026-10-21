// 平台核心共享类型（自 MusicServer sdk/types.ts 摘取 qs 用到的子集）

/** httpFetch 响应（与 lib/http.ts 契约一致） */
export interface HttpResponse {
  statusCode: number
  statusMessage?: string
  headers: Record<string, string | string[]>
  body: any
  raw?: Buffer
}

/** httpFetch 返回的可取消请求对象 */
export interface HttpRequestObject {
  promise: Promise<HttpResponse>
  cancelHttp(): void
}

/** httpFetch 选项（lib/http.ts 子集） */
export interface HttpOptions {
  method?: string
  headers?: Record<string, string>
  body?: string | object
  form?: Record<string, unknown>
  timeout?: number
  lookup?: unknown
  family?: number
}

/** 带取消句柄的异步结果（getMusicUrl/getLyric 等的统一返回形态） */
export interface RequestObj<T> {
  promise: Promise<T>
  cancelHttp(): void
}

/** 取流结果 */
export interface MusicUrlResult {
  type: string
  url: string
}
