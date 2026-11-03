import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { request, errorText } from '../api'
import type { PlatformState } from '../types'

export const usePlatform = defineStore('platform', () => {
  const data = ref<PlatformState | null>(null)
  const scene = ref('')
  const loading = ref(false)
  const error = ref('')
  // Credentials deliberately live only in memory, shared across Vue routes.
  const model = ref({
    base_url: '',
    api_key_value: '',
    model: '',
    temperature: 0.2,
    max_tokens: 2800,
    timeout_seconds: 120,
    retry_attempts: 3,
  })
  const modelConfigured = computed(() =>
    Boolean(
      model.value.base_url.trim() && model.value.api_key_value.trim() && model.value.model.trim(),
    ),
  )
  const currentScene = computed(() => data.value?.scenes.find((row) => row.name === scene.value))
  const protocols = computed(() => data.value?.protocols[scene.value] || [])
  const tasks = computed(() =>
    Object.entries(data.value?.task_profiles || {}).map(([value, item]) => ({ value, ...item })),
  )
  let pending: Promise<void> | null = null
  function refresh(): Promise<void> {
    if (pending) return pending
    loading.value = true
    error.value = ''
    pending = request<PlatformState>('/api/selfopt/state')
      .then((result) => {
        data.value = result
        if (!result.scenes.some((row) => row.name === scene.value))
          scene.value = result.default_scene || result.scenes[0]?.name || ''
      })
      .catch((cause) => {
        error.value = errorText(cause)
        throw cause
      })
      .finally(() => {
        loading.value = false
        pending = null
      })
    return pending
  }
  return {
    data,
    scene,
    loading,
    error,
    model,
    modelConfigured,
    currentScene,
    protocols,
    tasks,
    refresh,
  }
})
