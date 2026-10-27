/**
 * 合同上传客户端（Stage 12 后端接口的前端入口）。
 *
 * 红线：只调 Java（/api/v1/contracts，vite 已代理到 8080），绝不直连 Python 8000。
 * 字段严格按契约 contracts/openapi.yaml#ContractUploadResponse（snake_case），
 * 不在前端做驼峰转换——转换层会和契约漂移，出错时反而查不到源头。
 */

const BASE = '/api/v1/contracts'

export interface UploadContractResult {
  contract_id: string
  version: number
  filename: string
  content_type: string
  size_bytes: number
}

/**
 * 上传合同（首次上传得到 version=1）。
 *
 * 不设 Content-Type：必须由浏览器带上 multipart 的 boundary，手写会丢 boundary 导致 415。
 */
export async function uploadContract(file: File): Promise<UploadContractResult> {
  const form = new FormData()
  form.append('file', file)

  const response = await fetch(BASE, { method: 'POST', body: form })
  if (!response.ok) {
    // 后端 4xx 带 error body；把前 200 字带上，比只给状态码少一次抓包。
    const detail = await response.text().catch(() => '')
    throw new Error(`上传合同失败: HTTP ${response.status} ${detail.slice(0, 200)}`)
  }
  return (await response.json()) as UploadContractResult
}
