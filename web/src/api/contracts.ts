/**
 * 合同客户端（Stage 12）。
 *
 * 红线：只调 Java（/api/v1/contracts，vite 已代理到 8080），绝不直连 Python 8000。
 * 字段严格按契约 contracts/openapi.yaml（snake_case），不做驼峰转换。
 */

import type { ContractDetail, ContractDiff, ContractSummary } from '../types/api'
import { apiGet, apiUpload } from './http'

const BASE = '/api/v1/contracts'

export interface UploadContractResult {
  contract_id: string
  version: number
  filename: string
  content_type: string
  size_bytes: number
}

/**
 * 上传合同。传 contractId 即「覆盖上传」产生新版本（version+1），
 * 不传则新建合同（version=1）——与后端 `contract_id` 表单项语义一致。
 */
export async function uploadContract(file: File, contractId?: string): Promise<UploadContractResult> {
  const form = new FormData()
  form.append('file', file)
  if (contractId) form.append('contract_id', contractId)
  return apiUpload<UploadContractResult>(BASE, form)
}

export async function listContracts(keyword?: string, limit = 20): Promise<ContractSummary[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (keyword?.trim()) params.set('keyword', keyword.trim())
  const data = await apiGet<{ items?: ContractSummary[] }>(`${BASE}?${params.toString()}`)
  return data.items ?? []
}

export function getContract(id: string): Promise<ContractDetail> {
  return apiGet<ContractDetail>(`${BASE}/${id}`)
}

export function getContractDiff(id: string, target?: number): Promise<ContractDiff> {
  const query = target ? `?target=${target}` : ''
  return apiGet<ContractDiff>(`${BASE}/${id}/diff${query}`)
}

/**
 * 原文地址：直接交给 <iframe> / fetch 用，不经 fetch 包一层。
 * 必须走相对路径——前端只认 Java，由 vite 代理转发。
 */
export function contractContentUrl(id: string, version?: number): string {
  return version ? `${BASE}/${id}/content?version=${version}` : `${BASE}/${id}/content`
}

/** 新版本上传（改稿重审）的场景：同一 contract_id，版本号自增。 */
export function uploadNewVersion(id: string, file: File): Promise<UploadContractResult> {
  return uploadContract(file, id)
}
