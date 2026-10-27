/**
 * 管理端凭证（HTTP BASIC）。
 *
 * 为什么存 sessionStorage 而不是 localStorage：管理端是「运维偶尔打开、看完就关」的页面，
 * 凭证应该随标签页一起消失。localStorage 会让一台共用机器长期保留管理员口令。
 *
 * 为什么不能把它当安全边界：Base64 只做编码不做加密，能读 sessionStorage 的人就能还原口令。
 * 所以这一层只负责「怎么把凭证带给后端」，真正的门是后端的 401/403。
 */

const CREDENTIAL_KEY = 'clauseguard.admin.credential'
const USERNAME_KEY = 'clauseguard.admin.username'

/** sessionStorage 不可用（隐私模式、被策略禁用）时的退路：至少本次会话内可用。 */
const memory = new Map<string, string>()

function readStore(key: string): string | null {
  try {
    const store = typeof window === 'undefined' ? null : window.sessionStorage
    if (store) return store.getItem(key)
  } catch {
    // 连访问 sessionStorage 都会抛（部分浏览器的隐私模式），落到内存层
  }
  return memory.get(key) ?? null
}

function writeStore(key: string, value: string | null): void {
  if (value === null) memory.delete(key)
  else memory.set(key, value)

  try {
    const store = typeof window === 'undefined' ? null : window.sessionStorage
    if (!store) return
    if (value === null) store.removeItem(key)
    else store.setItem(key, value)
  } catch {
    // 写失败不影响本次会话：内存层已经写过了
  }
}

/**
 * Basic 头的载荷是 base64(Latin-1)，而 btoa 遇到非 Latin-1 字符直接抛 InvalidCharacterError。
 * 先按 UTF-8 取字节再 base64，中文账号名也能提交（Spring 侧默认按 UTF-8 解凭证）。
 */
export function encodeBasic(username: string, password: string): string {
  const bytes = new TextEncoder().encode(`${username}:${password}`)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return `Basic ${btoa(binary)}`
}

/** Authorization 头的值；没有凭证时返回 null，调用方据此决定带不带这一行。 */
export function getAuthHeader(): string | null {
  return readStore(CREDENTIAL_KEY)
}

export function getUsername(): string {
  return readStore(USERNAME_KEY) ?? ''
}

export function hasCredential(): boolean {
  return getAuthHeader() !== null
}

export function setCredential(username: string, password: string): void {
  writeStore(USERNAME_KEY, username)
  writeStore(CREDENTIAL_KEY, encodeBasic(username, password))
}

export function clearCredential(): void {
  writeStore(CREDENTIAL_KEY, null)
  writeStore(USERNAME_KEY, null)
}
