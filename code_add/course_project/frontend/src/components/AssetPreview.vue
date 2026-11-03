<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { usePlatform } from '../stores/platform'
import { request, query, errorText } from '../api'
import type { Preview } from '../types'
import JsonEvidence from './JsonEvidence.vue'
const props = defineProps<{ path: string }>()
const platform = usePlatform()
const normalized = computed(() => {
  const path = props.path.replaceAll('\\', '/')
  const root = platform.data?.project_root.replaceAll('\\', '/').replace(/\/$/, '')
  return root && path.toLowerCase().startsWith(root.toLowerCase() + '/')
    ? path.slice(root.length + 1)
    : path
})
const data = ref<Preview | null>(null),
  loading = ref(false),
  error = ref(''),
  page = ref(1)
let revision = 0
async function load() {
  const token = ++revision
  if (!props.path) {
    data.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await request<Preview>(
      query('/api/selfopt/preview', { path: normalized.value, page: page.value }),
    )
    if (token === revision) data.value = result
  } catch (cause) {
    if (token === revision) {
      error.value = errorText(cause)
      data.value = null
    }
  } finally {
    if (token === revision) loading.value = false
  }
}
watch(
  () => props.path,
  () => {
    page.value = 1
    void load()
  },
  { immediate: true },
)
</script>
<template>
  <div v-loading="loading">
    <div v-if="!path" class="empty-state">选择文件以预览内容</div>
    <div v-else>
      <div class="panel-heading">
        <span class="preview-title">{{ data?.name || normalized }}</span
        ><a :href="query('/api/selfopt/download', { path: normalized })" download>下载文件 ↓</a>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template v-if="data && !error">
        <img
          v-if="['image', 'png', 'jpg', 'jpeg', 'webp', 'svg'].includes(data.kind)"
          class="asset-image"
          :src="query('/api/selfopt/raw', { path: normalized })"
          :alt="data.name"
        />
        <iframe
          v-else-if="['html', 'pdf'].includes(data.kind)"
          class="asset-frame"
          sandbox=""
          :src="query('/api/selfopt/raw', { path: normalized })"
          :title="data.name"
        />
        <el-table v-else-if="data.headers && data.rows" :data="data.rows" max-height="500"
          ><el-table-column
            v-for="(header, index) in data.headers"
            :key="index"
            :label="header"
            min-width="130"
            ><template #default="{ row }">{{ row[index] }}</template></el-table-column
          ></el-table
        >
        <JsonEvidence v-else-if="data.kind === 'json'" :value="data.data" title="JSON 内容" />
        <pre v-else class="json">{{
          data.content || data.text || JSON.stringify(data.data ?? data, null, 2)
        }}</pre>
        <el-pagination
          v-if="(data.total || 0) > (data.page_size || 100)"
          v-model:current-page="page"
          layout="prev, pager, next"
          :page-size="data.page_size || 100"
          :total="data.total"
          @current-change="load"
        />
      </template>
    </div>
  </div>
</template>
