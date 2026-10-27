/**
 * 把请求失败翻译成人话。
 *
 * 为什么要单独一层：http.ts 统一抛 ApiError 时，拿不到结构化错误体就退化成 `HTTP 500`
 * 这种裸状态码。后端进程不在、网关 502、熔断 503 落到界面上都是同一串字符——
 * 用户看到「HTTP 500」只会以为是前端坏了，而真相是「服务没起来/连不上」。
 * 翻译规则集中在这里，免得每个页面各写一遍、各写一半。
 */

import type { ApiError } from '../api/http'

type ApiErrorLike = Pick<ApiError, 'status' | 'code' | 'message'>

/**
 * 为什么按形状判断而不是 `instanceof ApiError`：api/http.ts 的 import 链在 node --test
 * （原生 ESM、路径不带扩展名）下解析不了，测试一旦 import 它就整个崩掉。
 * 而这里真正依赖的只是「错误上带了 status/code 两个字段」，不关心它是哪个类的实例。
 */
function asApiError(error: unknown): ApiErrorLike | null {
  if (!(error instanceof Error)) return null
  const { status, code } = error as Error & { status?: unknown; code?: unknown }
  if (typeof status !== 'number' || typeof code !== 'string') return null
  return { status, code, message: error.message }
}

/** 结构化错误体缺失时 http.ts 会把 code 写成 `HTTP_<status>`，据此判断是否拿到了后端原话。 */
function hasServerMessage(error: ApiErrorLike): boolean {
  return !error.code.startsWith('HTTP_') && Boolean(error.message)
}

export function describeApiError(error: unknown, fallback: string): string {
  const apiError = asApiError(error)
  if (apiError) {
    // 后端/网关给了 {code,message}：原话比我们编的更准，直接透出。
    if (hasServerMessage(apiError)) return apiError.message
    switch (apiError.status) {
      case 401:
        return '需要登录凭证，请重新登录'
      case 403:
        return '当前账号没有这个操作的权限'
      case 404:
        return '没找到对应的资源'
      case 502:
      case 503:
      case 504:
        // 退避/熔断/网关在前端是同一个体感：暂时拿不到数据。说清「连不上后端」
        // 比报一个状态码有用——后者会让人去翻前端代码。
        return '连不上后端服务，请确认 java-service 是否在 8080 运行'
      default:
        if (apiError.status >= 500) return `后端出错了（HTTP ${apiError.status}），请稍后重试`
        return `请求失败（HTTP ${apiError.status}）`
    }
  }
  // fetch 直接 reject（进程没了、断网）不是 ApiError，落到 Error 分支。
  if (error instanceof Error && error.message) return error.message
  return fallback
}
