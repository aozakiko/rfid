import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { request, post, query, terminal, errorText } from '../api'
import type { Job } from '../types'

export const useJobs = defineStore('jobs', () => {
  const records = ref<Record<string, Job>>({})
  const errors = ref<Record<string, string>>({})
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const controllers = new Map<string, AbortController>()
  const history = computed(() =>
    Object.values(records.value).sort((a, b) => b.created_at.localeCompare(a.created_at)),
  )
  const active = computed(() => history.value.filter((row) => !terminal(row.status)))
  async function refresh() {
    const response = await request<{ jobs: Job[] }>('/api/selfopt/jobs')
    response.jobs.forEach((job) => {
      records.value[job.id] = job
      if (!terminal(job.status)) watch(job.id)
    })
  }
  function stopWatch(id: string) {
    const timer = timers.get(id)
    if (timer) clearTimeout(timer)
    timers.delete(id)
    controllers.get(id)?.abort()
    controllers.delete(id)
  }
  function watch(id: string) {
    if (timers.has(id) || controllers.has(id)) return
    async function poll() {
      timers.delete(id)
      const controller = new AbortController()
      controllers.set(id, controller)
      try {
        const { job } = await request<{ job: Job }>(query('/api/selfopt/job', { id }), {
          signal: controller.signal,
        })
        records.value[id] = job
        delete errors.value[id]
        if (terminal(job.status)) return
      } catch (error) {
        if (controller.signal.aborted) return
        errors.value[id] = errorText(error)
        // A transient connection error must not detach from accepted server work.
        if ('status' in Object(error) && (error as { status: number }).status === 404) return
      } finally {
        if (controllers.get(id) === controller) controllers.delete(id)
      }
      timers.set(id, setTimeout(poll, 1500))
    }
    void poll()
  }
  async function submit(endpoint: string, payload: unknown) {
    const { job } = await post<{ job: Job }>(endpoint, payload)
    records.value[job.id] = job
    watch(job.id)
    return job
  }
  async function cancel(id: string) {
    const { job } = await post<{ job: Job }>('/api/selfopt/job/cancel', { id })
    records.value[id] = job
  }
  function dispose() {
    ;[...timers.keys(), ...controllers.keys()].forEach(stopWatch)
  }
  return { records, errors, history, active, refresh, submit, watch, cancel, dispose }
})
