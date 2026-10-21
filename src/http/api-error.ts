// API 业务错误 —— 路由处理中抛出，由服务器统一转换为对应 HTTP 状态码的 JSON 响应

export class ApiError extends Error {
  /** HTTP 状态码（同时作为响应体中的 code） */
  status: number
  /** 与 status 同值，历史别名 */
  code: number
  /** 可选的结构化错误详情 */
  detail?: unknown

  constructor(status: number, msg: string, detail?: unknown) {
    super(msg)
    this.name = 'ApiError'
    this.status = status
    this.code = status
    this.detail = detail
  }
}

/** 资源不存在（未知路由 / 不支持的平台） */
export const notFound = (msg: string, detail?: unknown) => new ApiError(404, msg, detail)

/** 上游平台请求失败 */
export const upstream = (msg: string, detail?: unknown) => new ApiError(502, msg, detail)
