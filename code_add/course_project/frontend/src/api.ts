import type { ErrorInfo } from './types'
export class APIError extends Error {
  constructor(
    message: string,
    public status = 0,
    public info?: ErrorInfo,
  ) {
    super(message)
    this.name = 'APIError'
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(!(options.body instanceof Blob) ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new APIError('无法连接平台服务，请检查服务状态后重试。')
  }
  const data = await response.json().catch(() => {
    throw new APIError('服务响应格式异常，请重试。', response.status)
  })
  if (!response.ok) throw new APIError(data.error || '请求失败', response.status, data.error_info)
  return data as T
}
export function post<T>(path: string, data: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(data) })
}
export function query(path: string, values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return path + (params.size ? '?' + params.toString() : '')
}
export const terminal = (status: string) =>
  ['success', 'failed', 'cancelled', 'interrupted'].includes(status)
export const errorText = (error: unknown) =>
  error instanceof APIError && error.info
    ? `${error.message} ${error.info.suggestion}（${error.info.code} · ${error.info.request_id}）`
    : error instanceof Error
      ? error.message
      : '操作失败，请重试。'
export const format = (value: unknown, digits = 2) =>
  value === null || value === undefined || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits })
export const percent = (value: unknown) =>
  value === null || value === undefined ? '—' : format(Number(value) * 100, 1) + '%'
export const statusLabel = (value: string) =>
  ({
    queued: '排队中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
    cancelled: '已停止',
    interrupted: '已中断',
    passed: '通过',
    warning: '兼容告警',
    dry_run: '预览',
    execution_only: '仅执行验证',
    evaluated: '已评价',
    independence_not_established: '独立性未建立',
    disabled: '未启用',
    no_improvement: '连续无改进',
    iterations_completed: '完成指定轮数',
    model_request_budget: '模型请求预算耗尽',
    time_budget: '时间预算耗尽',
    target_communication_cost: '达到目标成本',
  })[value] || value
