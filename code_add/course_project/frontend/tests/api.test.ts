import { afterEach, describe, expect, it, vi } from 'vitest'
import { request, post, query, terminal, errorText } from '../src/api'

afterEach(() => vi.unstubAllGlobals())
describe('HTTP contract', () => {
  it('preserves server errors and never treats failed responses as data', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(new Response(JSON.stringify({ error: '版本已变化' }), { status: 409 })),
    )
    await expect(post('/api/selfopt/file', { revision: 'stale' })).rejects.toMatchObject({
      status: 409,
      message: '版本已变化',
    })
  })
  it('encodes filenames and hashes as query values', () => {
    const url = query('/api/selfopt/preview', { path: 'test/结果 #1.csv', page: 2 })
    expect(new URL(url, 'http://local').searchParams.get('path')).toBe('test/结果 #1.csv')
    expect(url).toContain('page=2')
  })
  it('retains actionable error details and request id across the HTTP boundary', async () => {
    const info = {
      code: 'candidate_source_changed',
      stage: 'freeze',
      message: '源码已变化',
      suggestion: '重新验证当前版本。',
      retryable: false,
      request_id: 'request-fixture',
    }
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ error: info.message, error_info: info }), { status: 409 }),
        ),
    )
    try {
      await post('/api/research/register', {})
    } catch (error) {
      expect(error).toMatchObject({ status: 409, info })
      expect(errorText(error)).toContain('重新验证当前版本。')
      expect(errorText(error)).toContain('request-fixture')
      return
    }
    throw new Error('request unexpectedly succeeded')
  })
  it('rejects invalid JSON and network failures with meaningful messages', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>offline</html>')))
    await expect(request('/api')).rejects.toThrow('服务响应格式异常')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(request('/api')).rejects.toThrow('无法连接平台服务')
  })
  it('treats cancellation and restart interruption as terminal', () => {
    for (const status of ['success', 'failed', 'cancelled', 'interrupted'])
      expect(terminal(status)).toBe(true)
    expect(terminal('running')).toBe(false)
  })
})
