<script setup lang="ts">
import { computed, reactive, ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { request, post, query, errorText, format, percent, terminal } from '../api'
import type { Asset, ComparisonRow, JsonObject } from '../types'
import ComparisonChart from '../components/ComparisonChart.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import JobPanel from '../components/JobPanel.vue'
import AssetPreview from '../components/AssetPreview.vue'
import CandidateActions from '../components/CandidateActions.vue'
interface Benchmark {
  available: boolean
  message?: string
  modes: Record<string, { rows: ComparisonRow[]; artifacts: Asset[] }>
  manifest: JsonObject
  provenance: JsonObject
  commands: unknown
}
interface Comparison {
  rows: ComparisonRow[]
  single_scenario: boolean
  case_count: number
  raw_count: number
}
const platform = usePlatform(),
  jobs = useJobs()
const benchmark = ref<Benchmark | null>(null),
  comparison = ref<Comparison | null>(null),
  busy = ref(false),
  error = ref(''),
  selected = ref(''),
  detailPage = ref(1),
  details = ref<{ rows: JsonObject[]; total: number } | null>(null),
  preview = ref(''),
  id = ref(''),
  candidateId = ref(''),
  registryId = ref('')
const filters = reactive({
  mode: 'main',
  task: 'mean',
  distribution: 'multi_peak',
  num_tags: '1000',
  scenario: '0|0|0',
  axis: 'mean_communication_bits',
})
const submitting = ref(false)
const taskActive = computed(
  () =>
    submitting.value || Boolean(id.value && !terminal(jobs.records[id.value]?.status || 'running')),
)
const modeRows = computed(() => benchmark.value?.modes[filters.mode]?.rows || [])
const options = computed(() => ({
  task: [...new Set(modeRows.value.map((row) => row.task))],
  distribution: [...new Set(modeRows.value.map((row) => String(row.distribution)))],
  num_tags: [...new Set(modeRows.value.map((row) => String(row.num_tags)))],
  scenario: [
    ...new Set(
      modeRows.value.map((row) => [row.missing_rate, row.noise_rate, row.extreme_rate].join('|')),
    ),
  ],
}))
const modeLabels: Record<string, string> = {
  main: '主实验',
  ablation: '消融实验',
  composite: '复合任务',
  sensitivity: '敏感度',
  robustness: '鲁棒性',
  blind: '盲测',
}
let sequence = 0,
  detailSequence = 0
async function load() {
  busy.value = true
  error.value = ''
  try {
    benchmark.value = await request<Benchmark>('/api/statistics/benchmark')
    if (!benchmark.value.available) {
      error.value = benchmark.value.message || '尚无统计实验结果'
      return
    }
    const record = benchmark.value.manifest.llm_record as JsonObject | undefined
    candidateId.value = candidateId.value || String(record?.candidate_id || '')
    await compare()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function compare() {
  if (!benchmark.value?.available) return
  const current = ++sequence
  comparison.value = null
  try {
    const result = await request<Comparison>(query('/api/statistics/comparison', filters))
    if (sequence === current) {
      comparison.value = result
      error.value = ''
    }
  } catch (cause) {
    if (sequence === current) error.value = errorText(cause)
  }
}
watch(
  () => filters.mode,
  () => {
    for (const key of ['task', 'distribution', 'num_tags', 'scenario'] as const) {
      if (!options.value[key].includes(filters[key]))
        filters[key] = key === 'task' ? options.value.task[0] || 'mean' : 'all'
    }
  },
)
watch(
  filters,
  () => {
    ++detailSequence
    void compare()
    selected.value = ''
    details.value = null
  },
  { deep: true },
)
async function showDetails(protocol: string, page = 1) {
  const current = ++detailSequence
  selected.value = protocol
  detailPage.value = page
  details.value = null
  try {
    const result = await request<{ rows: JsonObject[]; total: number }>(
      query('/api/statistics/details', { ...filters, protocol, page }),
    )
    if (current === detailSequence) details.value = result
  } catch (cause) {
    if (current === detailSequence) ElMessage.error(errorText(cause))
  }
}
async function submit(kind: string) {
  if (taskActive.value) return
  if (kind === 'statistics_llm' && !platform.modelConfigured) {
    ElMessage.error('请先到协议设计页配置模型')
    return
  }
  submitting.value = true
  try {
    id.value = (
      await jobs.submit(
        '/api/statistics/' + (kind === 'statistics_llm' ? 'generate-baseline' : 'run'),
        kind === 'statistics_llm'
          ? platform.model
          : {
              seed_count: 20,
              sizes: [1000, 10000, 100000],
              candidate_id: candidateId.value || undefined,
            },
      )
    ).id
  } catch (cause) {
    ElMessage.error(errorText(cause))
  } finally {
    submitting.value = false
  }
}
watch(
  () => jobs.records[id.value]?.status,
  (status) => {
    const job = jobs.records[id.value]
    if (status === 'success' && job) {
      if (job.kind === 'statistics_llm') {
        candidateId.value = String(job.result?.candidate_id || '')
        registryId.value = String(job.result?.registry_id || '')
      } else void load()
    }
  },
)
async function importCandidate(event: Event) {
  const target = event.target as HTMLInputElement,
    file = target.files?.[0]
  if (!file) return
  try {
    if (file.size > 100000) throw new Error('候选 JSON 最大 100 KB')
    const response = await post<{ candidate_id: string; registry_id: string }>(
      '/api/statistics/import-candidate',
      {
        record: JSON.parse(await file.text()),
      },
    )
    candidateId.value = response.candidate_id
    registryId.value = response.registry_id
    ElMessage.success('候选已导入')
  } catch (cause) {
    ElMessage.error(errorText(cause))
  } finally {
    target.value = ''
  }
}
onMounted(load)
</script>
<template>
  <section class="panel" v-loading="busy">
    <div class="panel-heading">
      <div>
        <h2>统计协议比较</h2>
        <p>前沿、置信区间和排除资格由后端计算，失败结果保留展示。</p>
      </div>
      <el-button @click="load">刷新基准</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" class="space-bottom" />
    <el-form
      label-position="top"
      class="form-grid"
      style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))"
    >
      <el-form-item label="实验类型"
        ><el-select v-model="filters.mode"
          ><el-option
            v-for="key in Object.keys(benchmark?.modes || {})"
            :key="key"
            :label="modeLabels[key] || key"
            :value="key" /></el-select
      ></el-form-item>
      <el-form-item label="统计任务"
        ><el-select v-model="filters.task"
          ><el-option v-for="value in options.task" :key="value" :value="value" /></el-select
      ></el-form-item>
      <el-form-item label="分布"
        ><el-select v-model="filters.distribution"
          ><el-option label="全部" value="all" /><el-option
            v-for="value in options.distribution"
            :key="value"
            :value="value" /></el-select
      ></el-form-item>
      <el-form-item label="标签规模"
        ><el-select v-model="filters.num_tags"
          ><el-option label="全部" value="all" /><el-option
            v-for="value in options.num_tags"
            :key="value"
            :value="value" /></el-select
      ></el-form-item>
      <el-form-item label="缺失 / 噪声 / 异常"
        ><el-select v-model="filters.scenario"
          ><el-option label="全部" value="all" /><el-option
            v-for="value in options.scenario"
            :key="value"
            :label="
              value
                .split('|')
                .map((n) => percent(Number(n)))
                .join(' / ')
            "
            :value="value" /></el-select
      ></el-form-item>
      <el-form-item label="通信比较轴"
        ><el-select v-model="filters.axis"
          ><el-option label="通信 bits" value="mean_communication_bits" /><el-option
            label="时隙 slots"
            value="mean_slots" /><el-option label="轮数 rounds" value="mean_rounds" /></el-select
      ></el-form-item>
    </el-form>
    <el-alert
      v-if="comparison"
      :title="
        comparison.single_scenario
          ? '同一场景比较：可以核对 Pareto 前沿与置信区间。'
          : '跨场景描述性汇总：不绘制 Pareto 前沿，请选择单一分布、规模与扰动。'
      "
      :type="comparison.single_scenario ? 'success' : 'info'"
      :closable="false"
    />
    <ComparisonChart
      :rows="comparison?.rows || []"
      :axis="filters.axis"
      :single-scenario="comparison?.single_scenario || false"
    />
    <el-table
      :data="comparison?.rows || []"
      @row-click="(row: ComparisonRow) => showDetails(row.protocol)"
      ><el-table-column prop="protocol" label="协议" min-width="170" /><el-table-column
        label="执行 / 语义通过"
        min-width="145"
        ><template #default="{ row }"
          >{{ percent(row.success_rate) }} / {{ percent(row.quality_pass_rate) }}</template
        ></el-table-column
      ><el-table-column label="误差均值 / 95% CI" min-width="180"
        ><template #default="{ row }"
          >{{ percent(row.mean_error) }}
          <div class="muted">
            {{ percent(row.error_ci95_low) }} ～ {{ percent(row.error_ci95_high) }}
          </div></template
        ></el-table-column
      ><el-table-column label="通信 bits" min-width="120"
        ><template #default="{ row }">{{
          format(row.mean_communication_bits, 0)
        }}</template></el-table-column
      ><el-table-column label="Pareto / 排除原因" min-width="200"
        ><template #default="{ row }"
          ><el-tag v-if="row.is_pareto" type="success">前沿</el-tag
          ><span v-else>{{
            row.pareto_eligible ? '可比较' : row.pareto_exclusion_reasons.join(' / ')
          }}</span></template
        ></el-table-column
      ></el-table
    >
    <p class="muted">
      {{ comparison?.case_count || 0 }} 个条件 ·
      {{ comparison?.raw_count || 0 }} 条逐次结果；点击协议查看原始记录。
    </p>
  </section>
  <section v-if="details" class="panel">
    <div class="panel-heading">
      <h2>{{ selected }} · 逐次证据</h2>
      <span class="muted">{{ details.total }} 条</span>
    </div>
    <JsonEvidence
      v-for="(row, index) in details.rows"
      :key="index"
      :value="row"
      :title="'Seed ' + row.seed + ' · ' + row.status"
    /><el-pagination
      v-model:current-page="detailPage"
      :total="details.total"
      :page-size="20"
      layout="prev, pager, next"
      @current-change="(page: number) => showDetails(selected, page)"
    />
  </section>
  <section class="panel">
    <div class="panel-heading"><h2>基准运行与候选导入</h2></div>
    <p class="muted">
      完整套件包含 20 个种子和三档规模，可能运行较长时间。真实 LLM
      候选复用模型配置；随机参数对照仍独立标注。
    </p>
    <div class="actions">
      <el-button type="primary" :disabled="taskActive" @click="submit('statistics_suite')"
        >运行完整统计套件</el-button
      ><el-button :disabled="taskActive" @click="submit('statistics_llm')"
        >生成真实 LLM 基线</el-button
      ><label
        >导入候选 JSON
        <input type="file" accept=".json" aria-label="导入统计候选 JSON" @change="importCandidate"
      /></label>
    </div>
    <p v-if="candidateId" class="path">当前候选：{{ candidateId }}</p>
    <CandidateActions v-if="registryId" :candidate-id="registryId" family="statistics" />
    <JsonEvidence
      :value="{
        manifest: benchmark?.manifest,
        provenance: benchmark?.provenance,
        commands: benchmark?.commands,
      }"
      title="统计来源、审计与复现命令"
    />
    <div class="actions">
      <el-button
        v-for="asset in benchmark?.modes[filters.mode]?.artifacts || []"
        :key="asset.path"
        link
        @click="preview = asset.path"
        >{{ asset.name }}</el-button
      >
    </div>
  </section>
  <JobPanel v-if="id" :id="id" />
  <section v-if="preview" class="panel"><AssetPreview :path="preview" /></section>
</template>
