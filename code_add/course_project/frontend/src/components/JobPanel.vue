<script setup lang="ts">
import { computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useJobs } from '../stores/jobs'
import { terminal, errorText, statusLabel } from '../api'
import StatusTag from './StatusTag.vue'
import { query } from '../api'
const props = defineProps<{ id: string }>()
const jobs = useJobs()
const job = computed(() => jobs.records[props.id])
watch(
  () => props.id,
  (id) => {
    if (id) jobs.watch(id)
  },
  { immediate: true },
)
async function cancel() {
  try {
    await jobs.cancel(props.id)
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
</script>
<template>
  <section v-if="job" class="panel" aria-label="任务进度">
    <div class="panel-heading">
      <div>
        <h2>后台任务进度 <StatusTag :status="job.status" /></h2>
        <p>{{ job.kind }} · {{ job.elapsed_s }} 秒 · {{ job.stage }}</p>
      </div>
      <el-button v-if="!terminal(job.status)" :disabled="job.cancel_requested" @click="cancel">{{
        job.cancel_requested ? '等待停止' : '停止任务'
      }}</el-button>
    </div>
    <el-progress
      :percentage="job.progress"
      :status="
        job.status === 'failed' ? 'exception' : job.status === 'success' ? 'success' : undefined
      "
      :stroke-width="7"
    />
    <p v-if="job.error || jobs.errors[id]" class="error-message">
      {{ job.error || jobs.errors[id] }}
    </p>
    <el-alert
      v-if="job.error_info"
      :title="job.error_info.suggestion"
      type="warning"
      :closable="false"
    >
      {{ job.error_info.code }} · 失败阶段 {{ job.error_info.stage }} · 错误编号
      {{ job.error_info.request_id }}
    </el-alert>
    <p v-if="job.result?.partial" class="muted">
      已保留部分结果，完整性未通过前不参与正式排名。已完成
      {{ job.result.completed ?? job.result.completed_runs ?? '当前阶段' }}。
      <a
        v-if="job.result.evidence_path"
        :href="query('/api/selfopt/download', { path: String(job.result.evidence_path) })"
        >下载已完成证据</a
      >
    </p>
    <pre class="console" role="log" aria-live="polite">{{
      job.logs.join('\n') || statusLabel(job.status)
    }}</pre>
  </section>
</template>
