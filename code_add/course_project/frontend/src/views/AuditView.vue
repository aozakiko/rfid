<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useJobs } from '../stores/jobs'
import { usePlatform } from '../stores/platform'
import { request, errorText } from '../api'
import type { JsonObject, Job } from '../types'
import StatusTag from '../components/StatusTag.vue'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
const jobs = useJobs(),
  platform = usePlatform(),
  database = ref<JsonObject | null>(null),
  lineage = ref<JsonObject | null>(null),
  selected = ref(''),
  search = ref(''),
  page = ref(1)
const filtered = computed(() =>
  jobs.history.filter(
    (job) =>
      !search.value ||
      (job.id + job.kind + String(job.payload.prefix || '')).includes(search.value),
  ),
)
async function load() {
  try {
    ;[database.value, lineage.value] = await Promise.all([
      request<JsonObject>('/api/database'),
      request<JsonObject>('/api/selfopt/candidates'),
    ])
    await jobs.refresh()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
async function retry(id: string) {
  try {
    selected.value = (await jobs.submit('/api/selfopt/job/retry', { id, ...platform.model })).id
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
onMounted(load)
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>后台任务历史</h2>
        <p>重启中断任务不会自动重发模型请求；重新运行使用新名称和当前凭据。</p>
      </div>
      <el-button @click="load">刷新记录</el-button>
    </div>
    <el-input
      v-model="search"
      placeholder="搜索任务 ID、类型或前缀"
      clearable
      @input="page = 1"
    /><el-table
      :data="filtered.slice((page - 1) * 10, page * 10)"
      @row-click="(row: Job) => (selected = row.id)"
      ><el-table-column prop="created_at" label="创建时间" min-width="160" /><el-table-column
        prop="kind"
        label="任务类型"
        min-width="150"
      /><el-table-column label="ID" min-width="140"
        ><template #default="{ row }"
          ><span class="path">{{ row.id }}</span></template
        ></el-table-column
      ><el-table-column label="状态" width="100"
        ><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column
      ><el-table-column label="操作" width="130"
        ><template #default="{ row }"
          ><el-button
            v-if="
              ['failed', 'cancelled', 'interrupted'].includes(row.status) &&
              ['auto_iterate', 'ai_generation'].includes(row.kind)
            "
            link
            @click.stop="retry(row.id)"
            >新名称重新运行</el-button
          ><el-button v-else link @click.stop="selected = row.id">查看</el-button></template
        ></el-table-column
      ></el-table
    ><el-pagination
      v-model:current-page="page"
      :total="filtered.length"
      :page-size="10"
      layout="prev, pager, next"
    />
  </section>
  <JobPanel v-if="selected" :id="selected" />
  <section v-if="selected && jobs.records[selected]?.result" class="panel">
    <JsonEvidence :value="jobs.records[selected]?.result" title="任务结果" />
  </section>
  <div class="split">
    <section class="panel">
      <div class="panel-heading"><h2>候选来源与谱系</h2></div>
      <p class="muted">{{ lineage?.note }}</p>
      <JsonEvidence :value="lineage" title="候选记录、实际父代边与来源核对" />
    </section>
    <section class="panel">
      <div class="panel-heading"><h2>系统记录</h2></div>
      <JsonEvidence :value="database" title="运行计数、验证历史与系统事件" />
      <p class="muted">模型密钥只存在当前页面内存；任务历史、路由和源码不保存模型密钥。</p>
    </section>
  </div>
</template>
