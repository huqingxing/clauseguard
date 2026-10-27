/**
 * 管理端老链接兼容：阶段 3 用手切 hash 记视图（`admin.html#eval`），
 * 阶段 4 换成正式的 hash 路由（`admin.html#/eval`）。
 *
 * 为什么必须显式迁移：`#eval` 在新路由表里不是任何一条 path，
 * 会被 catch-all 兜到 `/health`——用户点的是「评测看板」的书签，
 * 落在运维屏，且地址栏看不出原因。
 *
 * 只补一个前导斜杠：空 hash 交给路由的 `/` 默认跳转，
 * 已带斜杠的本来就是新格式，带查询串的（`#eval?x=1`）也只需要补斜杠。
 */
export function toHashRoute(hash: string): string {
  if (!hash.startsWith('#')) return hash
  const raw = hash.slice(1)
  if (raw === '' || raw.startsWith('/')) return hash
  return `#/${raw}`
}
