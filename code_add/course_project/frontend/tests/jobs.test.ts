import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useJobs } from '../src/stores/jobs'
import { usePlatform } from '../src/stores/platform'
import type { Job } from '../src/types'

const base = {
  id: 'fixture',
  kind: 'auto_iterate',
  status: 'running',
  stage: 'model',
  progress: 40,
  elapsed_s: 1,
  created_at: '2026-11-03T10:00:00',
  payload: {},
  logs: [],
  error: '',
  result: null,
} satisfies Job
beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})
afterEach(() => {
  useJobs().dispose()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
describe('background work', () => {
  it('uses a durable terminal workspace snapshot without requesting pruned history', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const jobs = useJobs()
    jobs.records[base.id] = { ...base, status: 'success' }
    jobs.watch(base.id)
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetch).not.toHaveBeenCalled()
    expect(jobs.records[base.id].status).toBe('success')
  })
  it('recovers after a transient network failure and stops polling cancelled work', async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ job: { ...base, status: 'cancelled' } })),
      )
    vi.stubGlobal('fetch', fetch)
    const jobs = useJobs()
    jobs.watch(base.id)
    await vi.advanceTimersByTimeAsync(1)
    expect(jobs.errors[base.id]).toContain('无法连接')
    await vi.advanceTimersByTimeAsync(1500)
    expect(jobs.records[base.id].status).toBe('cancelled')
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('keeps one poller when two views observe the same task', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ job: base }))))
    const jobs = useJobs()
    jobs.watch(base.id)
    jobs.watch(base.id)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('models credentials stay in store memory and can be cleared', () => {
    const platform = usePlatform()
    platform.model = {
      ...platform.model,
      base_url: 'https://fixture.invalid',
      api_key_value: 'test-secret',
      model: 'offline',
    }
    expect(platform.modelConfigured).toBe(true)
    platform.model.api_key_value = ''
    expect(platform.modelConfigured).toBe(false)
  })
})
