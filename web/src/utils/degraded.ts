/**
 * 未审条款提示文案（Stage 15）。
 *
 * 为什么单独成文件：这是「结论不完整」这件事在界面上唯一的出口，文案说错比不显示更糟
 * ——把「模型不可用」说成「合同没问题」，正是这次要修的那个 bug 的界面版本。
 * 抽成纯函数才能在不引组件测试框架的前提下钉住分支（web/tests 用 node:test）。
 *
 * 两个数的口径不同，别混：
 * - unreviewed_clauses：按条款去重（同一条款任一专家审成即不计入）；
 * - llm_errors：按「条款×专家」单元计，通常 ≥ unreviewed_clauses。
 * 只有 llm_errors 覆盖到每一条未审条款时，才敢说「因模型不可用」；否则不替原因下结论。
 */

export interface DegradedDetailLike {
  unreviewed_clauses: number
  llm_errors: number
}

export function unreviewedNotice(detail?: DegradedDetailLike | null): string {
  if (!detail || detail.unreviewed_clauses <= 0) return ''
  const count = detail.unreviewed_clauses
  if (detail.llm_errors >= count) {
    return `本次有 ${count} 条因模型不可用未审查，结果可能不完整`
  }
  return `本次有 ${count} 条未完成审查，结果可能不完整`
}
