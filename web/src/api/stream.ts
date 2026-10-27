/**
 * 事件流的重连策略与去重判定——纯函数，无 DOM / 网络依赖。
 *
 * 为什么从 reviews.ts 抽出来：subscribeReview 依赖 EventSource，只能在浏览器里跑；
 * 但「退避多久」「这条 seq 要不要丢」是最容易写错、也最该被测试的部分，
 * 抽成纯函数后可以用 Node 内置测试直接跑，不必为此引入测试框架（铁律 5：不引入版本表之外的依赖）。
 */

const BASE_DELAY_MS = 1000
const MAX_DELAY_MS = 30_000
/** 2^5 = 32 倍已超过 30s 上限，再往上只是无意义的乘法。 */
const MAX_EXPONENT = 5

/**
 * 第 n 次重连前等待多久：1s → 2s → 4s → 8s → 16s → 30s（此后恒定 30s）。
 *
 * 为什么要退避：下游熔断时 /stream 返回 503，浏览器对 EventSource 会静默重试，
 * 固定间隔重试会在熔断期间持续打满网关；30s 封顶保证恢复后最迟半分钟回到正常。
 */
export function retryDelayMs(attempt: number): number {
  const exponent = Math.min(Math.max(attempt - 1, 0), MAX_EXPONENT)
  return Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** exponent)
}

/**
 * 是否丢弃该事件：是否已经被处理过（按 seq 单调性判断）。
 *
 * 为什么必须去重：手动重建 EventSource 不会带 Last-Event-ID，Java 的 /stream 支持
 * 从 seq=0 全量回放，因此每次重连都会重收一遍已经处理过的前缀——不去重就会把
 * 同一个 node.finished 计两次、摘要翻倍。
 *
 * 没有 seq 的帧（契约之外）一律放行，交由展示层忽略，不让畸形帧卡住整条流。
 */
export function shouldSkipSeq(seq: number | undefined, lastSeq: number): boolean {
  return typeof seq === 'number' && seq <= lastSeq
}
