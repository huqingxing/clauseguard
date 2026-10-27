/**
 * 本地身份（审批留痕用）。
 *
 * 项目没有登录体系（Stage 12 范围外），但审批必须带操作人写入审计表——
 * 因此这里只做「操作人署名」的持久化，不是认证。放在 localStorage 是为了
 * 手机审批页刷新后不用重复输入。
 */

import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

const STORAGE_KEY = 'clauseguard.operator'

function readOperator(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    // 隐私模式下 localStorage 可能抛异常，退化为不持久化。
    return ''
  }
}

export const useSessionStore = defineStore('session', () => {
  const operator = ref(readOperator())

  watch(operator, (value) => {
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 无法持久化不影响本次审批（值仍在内存里）。
    }
  })

  return { operator }
})
