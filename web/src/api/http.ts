/**
 * 统一 HTTP 客户端。
 *
 * 为什么要有这一层：错误处理散落在每个调用点会漂移（有的只抛状态码、有的吞掉）。
 * 这里把「错误体 {code,message} 解析」「非 JSON 响应的兜底」「409 冲突」集中一次，
 * 上层只判 ApiError.status。
 */

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

interface ErrorBody {
  code?: unknown
  message?: unknown
}

async function toApiError(response: Response): Promise<ApiError> {
  // 后端 4xx/5xx 带 {code,message}；网关/代理层可能返回 HTML，取前 200 字兜底。
  const raw = await response.text().catch(() => '')
  try {
    const body = JSON.parse(raw) as ErrorBody
    const code = typeof body.code === 'string' ? body.code : `HTTP_${response.status}`
    const message = typeof body.message === 'string' ? body.message : raw.slice(0, 200)
    return new ApiError(response.status, code, message || code)
  } catch {
    return new ApiError(response.status, `HTTP_${response.status}`, raw.slice(0, 200) || `HTTP ${response.status}`)
  }
}

async function send(method: string, path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, method })
  if (!response.ok) throw await toApiError(response)
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
