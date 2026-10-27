/**
 * 统一 HTTP 客户端。
 *
 * 为什么要有这一层：错误处理散落在每个调用点会漂移（有的只抛状态码、有的吞掉）。
 * 这里把「错误体 {code,message} 解析」「非 JSON 响应的兜底」「409 冲突」「管理端凭证」
 * 集中一次，上层只判 ApiError.status / instanceof AuthError。
 */

import { getAuthHeader } from './auth'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/**
 * 401 单独一个类型，因为它的处理方式与业务错误不同：不是弹个提示让人重试，
 * 而是清凭证、退回登录卡。混在 ApiError 里，每个页面都得自己判一次状态码。
 */
export class AuthError extends ApiError {
  constructor(status: number, code: string, message: string) {
    super(status, code, message)
    this.name = 'AuthError'
  }
}

type UnauthorizedHandler = (error: AuthError) => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/** 管理端入口注册一次即可；用户端不注册，401 在那里就只是一次普通失败。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

interface ErrorBody {
  code?: unknown
  message?: unknown
}

/** 只对管理端接口带凭证：用户端接口是 permitAll，无差别携带等于把管理员口令撒到每个请求上。 */
const ADMIN_PATH = /^\/api\/v1\/admin(?:\/|$)/

async function toApiError(response: Response): Promise<ApiError> {
  // 后端 4xx/5xx 带 {code,message}；网关/代理层可能返回 HTML，取前 200 字兜底。
  const raw = await response.text().catch(() => '')
  let code = `HTTP_${response.status}`
  let message = raw.slice(0, 200) || `HTTP ${response.status}`
  try {
    const body = JSON.parse(raw) as ErrorBody
    if (typeof body.code === 'string') code = body.code
    if (typeof body.message === 'string' && body.message) message = body.message
  } catch {
    // 非 JSON（HTML 错误页 / 空体）保留上面的兜底值
  }
  return response.status === 401
    ? new AuthError(401, code, message)
    : new ApiError(response.status, code, message)
}

async function send(method: string, path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (ADMIN_PATH.test(path)) {
    const auth = getAuthHeader()
    if (auth) headers.set('Authorization', auth)
  }

  // credentials: 'omit' 不是为了「不带 cookie」（本服务 STATELESS，本来就没有），而是为了让浏览器
  // 不要接管 401：响应里带 WWW-Authenticate 时，默认的 same-origin 会触发浏览器原生 Basic 登录框，
  // 而 fetch 在无头环境/弹框无人应答时会一直挂着——实测 4s 都不返回，页面表现是「点了登录没反应」。
  // 关掉凭证模式后同一个 401 约 111ms 就交回 JS，登录卡才能自己说「账号或密码错误」。
  const response = await fetch(path, { ...init, method, headers, credentials: 'omit' })
  if (!response.ok) {
    const error = await toApiError(response)
    // 先通知再抛：调用方可能 catch 后就地渲染错误，界面得先翻到登录卡，
    // 否则会短暂出现「健康检查失败：401」这种把认证问题说成故障的错觉。
    if (error instanceof AuthError) unauthorizedHandler?.(error)
    throw error
  }
  return response
}

export async function apiGet<T>(path: string): Promise<T> {
  return (await (await send('GET', path)).json()) as T
}

export async function apiPost<T>(path: string, body: unknown, headers: Record<string, string> = {}): Promise<T> {
  const response = await send('POST', path, {
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  })
  return (await response.json()) as T
}

/** multipart 上传：不设 Content-Type，让浏览器带 boundary（手写会丢 boundary 导致 415）。 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const response = await send('POST', path, { body: form })
  return (await response.json()) as T
}
