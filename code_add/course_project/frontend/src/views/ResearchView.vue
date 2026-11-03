<script setup lang="ts">
import { computed, reactive, ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { request, post, query, errorText, terminal, percent, format, statusLabel } from '../api'
import type { RegisteredCandidate, ResearchRun, JsonObject } from '../types'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import AssetPreview from '../components/AssetPreview.vue'
import CandidateActions from '../components/CandidateActions.vue'
import MetricChart from '../components/MetricChart.vue'
const route = useRoute(),
  platform = usePlatform(),
  jobs = useJobs()
interface Capabilities {
  families: Record<string, { label: string; profiles: string[]; cost_model: string }>
  distributions: string[]
}
const capabilities = ref<Capabilities>(),
  candidates = ref<RegisteredCandidate[]>([]),
  history = ref<JsonObject[]>([]),
  report = ref<ResearchRun | null>(null)
const family = ref('python'),
  ids = ref<string[]>([]),
  baseline = ref(''),
  sourceProtocol = ref('MeasuredHistogramMean'),
  id = ref(''),
  busy = ref(false),
  preview = ref(''),
  split = ref('blind'),
  search = ref('')
const form = reactive({
  task_profile: 'mean_estimation',
  distributions: ['multi_peak', 'uniform'],
  sizes: '80,200',
  validation_seeds: '1000001,1000002',
  blind_seeds: '2000001,2000002',
  development_seeds: '10,11',
  price_min: 1,
  price_max: 64,
  relative_tolerance: 0.15,
  absolute_tolerance: 2,
  minimum_pass_rate: 1,
  quantile: 0.5,
  max_seconds: 1800,
  timeout_seconds: 10,
  disturbance: true,
  missing_rate: 0.1,
  noise_rate: 0.1,
  extreme_rate: 0,
})
const available = computed(() =>
  candidates.value.filter(
    (row) => row.family === family.value && row.supported_profiles.includes(form.task_profile),
  ),
)
const shownCandidates = computed(() =>
  available.value.filter(
    (row) =>
      !search.value ||
      (row.name + row.source_path + row.id).toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const job = computed(() => jobs.records[id.value]),
  active = computed(() => Boolean(job.value && !terminal(job.value.status)))
const filteredRows = computed(
  () => report.value?.rows.filter((row) => row.split === split.value) || [],
)
const scenarios = computed(() => [
  ...new Set(
    filteredRows.value.map((row) => [row.distribution, row.num_tags, row.perturbation].join(' · ')),
  ),
])
const chartSeries = computed(() =>
  (report.value?.manifest.candidates || []).map((candidate) => ({
    name: candidate.name + ' · ' + candidate.id.slice(0, 6),
    data: scenarios.value.map((key) => {
      const row = filteredRows.value.find(
        (row) =>
          row.candidate_id === candidate.id &&
          [row.distribution, row.num_tags, row.perturbation].join(' · ') === key,
      )
      return row?.eligible ? row.mean_cost_ms : null
    }),
  })),
)
const independenceLabels: Record<string, string> = {
  verified_against_recorded_history: '与已记录搜索及验证数据无重叠',
  overlap_detected: '发现数据重叠，独立性未通过',
  not_established: '未建立独立性：历史不完整或执行未完成',
}
const exclusionLabels: Record<string, string> = {
  incomplete_runs: '执行次数不完整',
  quality_threshold_not_met: '未达到精度门槛',
  unmeasured_or_different_channel: '通信未计量或模型不同',
  dataset_overlaps_recorded_search_or_validation: '与已记录搜索或验证数据重叠',
  missing_or_repeated_datasets: '数据缺失或重复',
  validation_blind_dataset_overlap: '验证与盲测数据重叠',
  run_incomplete_or_integrity_failed: '实验未完成或文件审计失败',
}
let appliedQuery = ''
async function load() {
  try {
    const [caps, registry, runs] = await Promise.all([
      request<Capabilities>('/api/research/capabilities'),
      request<{ candidates: RegisteredCandidate[] }>('/api/research/candidates'),
      request<{ runs: JsonObject[] }>('/api/research/runs'),
    ])
    capabilities.value = caps
    candidates.value = registry.candidates
    history.value = runs.runs
    applyQuery()
    if (
      route.name === 'research' &&
      typeof route.query.run === 'string' &&
      report.value?.manifest.run_id !== route.query.run
    )
      await loadRun(route.query.run)
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
function applyQuery() {
  if (route.name !== 'research' || !route.query.candidate) return
  const key = String(route.query.candidate) + ':' + String(route.query.baseline || '')
  if (key === appliedQuery) return
  const selected = candidates.value.find((row) => row.id === route.query.candidate)
  if (!selected) return
  appliedQuery = key
  if (selected.scene) platform.scene = selected.scene
  family.value = selected.family
  form.task_profile = selected.task_profile || 'mean_estimation'
  ids.value = [selected.id]
  const base = candidates.value.find(
    (row) =>
      row.id === route.query.baseline &&
      row.family === selected.family &&
      row.supported_profiles.includes(form.task_profile),
  )
  if (base && base.id !== selected.id) ids.value.push(base.id)
  baseline.value = base?.id || selected.id
  if (family.value === 'statistics') form.disturbance = false
}
watch(
  () => [route.name, route.query.candidate, route.query.baseline, route.query.run],
  () => {
    if (route.name === 'research') void load()
  },
)
watch(
  () => platform.scene,
  () => {
    sourceProtocol.value = platform.protocols.includes('MeasuredHistogramMean')
      ? 'MeasuredHistogramMean'
      : platform.protocols[0] || ''
  },
)
watch(family, () => {
  const supported = capabilities.value?.families[family.value]?.profiles || []
  if (!supported.includes(form.task_profile)) form.task_profile = supported[0] || 'mean_estimation'
})
watch([family, () => form.task_profile], () => {
  ids.value = ids.value.filter((id) => available.value.some((row) => row.id === id))
  if (!ids.value.includes(baseline.value)) baseline.value = ids.value[0] || ''
  if (family.value === 'statistics') form.disturbance = false
})
async function register() {
  busy.value = true
  try {
    const result = await post<{ candidate: RegisteredCandidate }>('/api/research/register', {
      scene: platform.scene,
      protocol: sourceProtocol.value,
      task_profile: form.task_profile,
      family: 'python',
    })
    await load()
    if (!ids.value.includes(result.candidate.id)) ids.value.push(result.candidate.id)
    baseline.value ||= result.candidate.id
    ElMessage.success('源码版本已冻结登记')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
function numbers(text: string) {
  const values = text.split(',').map((s) => s.trim())
  if (values.some((s) => !s || !/^\d+$/.test(s)))
    throw new Error('规模和 Seeds 请填写逗号分隔的非负整数')
  return values.map(Number)
}
async function run() {
  if (busy.value || active.value) return
  busy.value = true
  try {
    if (!ids.value.length) throw new Error('请先选择冻结候选')
    const clean = { missing_rate: 0, noise_rate: 0, extreme_rate: 0 }
    const payload = {
      ...form,
      candidate_ids: ids.value,
      baseline_id: baseline.value || ids.value[0],
      sizes: numbers(form.sizes),
      validation_seeds: numbers(form.validation_seeds),
      blind_seeds: numbers(form.blind_seeds),
      development_seeds: numbers(form.development_seeds),
      perturbations: form.disturbance
        ? [
            clean,
            {
              missing_rate: form.missing_rate,
              noise_rate: form.noise_rate,
              extreme_rate: form.extreme_rate,
            },
          ]
        : [clean],
    }
    id.value = (await jobs.submit('/api/research/evaluate-async', payload)).id
    report.value = null
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function loadRun(runId: string) {
  try {
    report.value = await request<ResearchRun>(query('/api/research/runs', { id: runId }))
    preview.value = ''
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
function assetLabel(name: string) {
  const candidate = report.value?.manifest.candidates.find((row) => name === row.id + '.py')
  return candidate ? '源码 · ' + candidate.name + ' · ' + candidate.id.slice(0, 8) : name
}
watch(
  () => job.value?.status,
  async (status) => {
    if (status && terminal(status)) {
      const runId = String(job.value?.result?.run_id || '')
      if (job.value?.kind === 'research_evaluation' && runId) await loadRun(runId)
      await load()
    }
  },
)
onMounted(load)
</script>
<template>
  <el-alert
    title="所有候选在实验前冻结。通过率与通信成本只在同一任务、场景和计量模型内比较；实验结果不反馈给已经结束的搜索。"
    type="info"
    :closable="false"
    class="space-bottom"
  />
  <div class="split">
    <section class="panel">
      <div class="panel-heading">
        <h2>候选版本库</h2>
        <el-button @click="load">刷新候选</el-button>
      </div>
      <el-form label-position="top" class="form-grid">
        <el-form-item label="候选类型"
          ><el-select v-model="family"
            ><el-option
              v-for="(item, key) in capabilities?.families"
              :key="key"
              :label="item.label"
              :value="String(key)" /></el-select
        ></el-form-item>
        <el-form-item label="评测任务"
          ><el-select v-model="form.task_profile"
            ><el-option
              v-for="task in platform.tasks.filter((row) =>
                capabilities?.families[family]?.profiles.includes(row.value),
              )"
              :key="task.value"
              :label="task.title"
              :value="task.value" /></el-select
        ></el-form-item>
        <template v-if="family === 'python'"
          ><el-form-item label="登记已有协议" class="full"
            ><el-select v-model="sourceProtocol" filterable
              ><el-option
                v-for="name in platform.protocols"
                :key="name"
                :value="name" /></el-select></el-form-item
          ><el-button :loading="busy" @click="register">冻结并登记协议</el-button></template
        >
        <el-form-item label="参与候选版本" class="full"
          ><el-select
            v-model="ids"
            multiple
            :multiple-limit="family === 'statistics' ? 1 : 6"
            filterable
            aria-label="参与候选版本"
            ><el-option
              v-for="row in available"
              :key="row.id"
              :label="row.name + ' · ' + row.id.slice(0, 8)"
              :value="row.id" /></el-select
        ></el-form-item>
        <el-form-item v-if="family === 'python'" label="对照基线版本" class="full"
          ><el-select v-model="baseline"
            ><el-option
              v-for="row in available.filter((row) => ids.includes(row.id))"
              :key="row.id"
              :label="row.name + ' · ' + row.id.slice(0, 8)"
              :value="row.id" /></el-select
        ></el-form-item>
      </el-form>
      <el-input v-model="search" placeholder="搜索候选名、版本或路径" clearable />
      <el-table :data="shownCandidates" max-height="330"
        ><el-table-column label="冻结版本" min-width="200"
          ><template #default="{ row }"
            ><strong>{{ row.name }}</strong>
            <div class="path">{{ row.id.slice(0, 12) }} · {{ row.scene }}</div>
            <div class="muted">
              {{
                row.search_history_known
                  ? '已记录搜索历史 / 原始基线'
                  : '搜索历史未知，不宣称独立泛化'
              }}
            </div></template
          ></el-table-column
        ><el-table-column label="后续操作" width="160"
          ><template #default="{ row }"
            ><CandidateActions
              :candidate-id="row.id"
              :family="row.family" /></template></el-table-column
      ></el-table>
      <p class="muted">
        Python 候选的登记任务由你选择，严格评测会核对实际输出。统计 JSON
        使用既有统计算子流水线，原有排行榜保持独立。
      </p>
    </section>
    <section class="panel">
      <div class="panel-heading">
        <h2>跨场景验证与盲测</h2>
        <el-tag>{{ family === 'python' ? '严格 Python 信道' : '统计基准流水线' }}</el-tag>
      </div>
      <el-form label-position="top" class="form-grid">
        <el-form-item label="分布" class="full"
          ><el-select v-model="form.distributions" multiple
            ><el-option
              v-for="name in capabilities?.distributions"
              :key="name"
              :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="标签规模"><el-input v-model="form.sizes" /></el-form-item
        ><el-form-item label="时间预算（秒）"
          ><el-input-number v-model="form.max_seconds" :min="1" :max="3600"
        /></el-form-item>
        <el-form-item v-if="family === 'statistics'" label="统计开发 Seeds" class="full"
          ><el-input v-model="form.development_seeds"
        /></el-form-item>
        <el-form-item label="独立验证 Seeds"
          ><el-input v-model="form.validation_seeds" /></el-form-item
        ><el-form-item label="盲测 Seeds"><el-input v-model="form.blind_seeds" /></el-form-item>
        <el-form-item label="属性下限"
          ><el-input-number v-model="form.price_min" :min="1" /></el-form-item
        ><el-form-item label="属性上限"
          ><el-input-number v-model="form.price_max" :min="3"
        /></el-form-item>
        <template v-if="family === 'python'"
          ><el-form-item label="相对误差门槛"
            ><el-input-number
              v-model="form.relative_tolerance"
              :min="0"
              :max="1"
              :step="0.05" /></el-form-item
          ><el-form-item label="绝对误差门槛"
            ><el-input-number v-model="form.absolute_tolerance" :min="0"
          /></el-form-item>
          <el-form-item label="最低通过率"
            ><el-input-number
              v-model="form.minimum_pass_rate"
              :min="0"
              :max="1"
              :step="0.05" /></el-form-item
          ><el-form-item label="单次执行超时（秒）"
            ><el-input-number v-model="form.timeout_seconds" :min="1" :max="60" /></el-form-item
        ></template>
        <el-form-item v-if="form.task_profile === 'median_quantile'" label="目标分位数"
          ><el-input-number v-model="form.quantile" :min="0" :max="1" :step="0.05"
        /></el-form-item>
      </el-form>
      <el-alert
        v-if="family === 'statistics'"
        title="统计候选与 polling、shared_sketch 内置基线运行开发、验证、缺失、噪声、异常、盲测及双次复现。精度采用统计引擎的既有任务准则，额外阶段使用流水线配置，结果在统计实验页面查看。"
        type="info"
        :closable="false"
      />
      <template v-if="family === 'python'"
        ><el-checkbox v-model="form.disturbance">同时运行缺失 / 噪声 / 异常扰动</el-checkbox
        ><el-form v-if="form.disturbance" label-position="top" class="form-grid"
          ><el-form-item label="缺失率"
            ><el-input-number
              v-model="form.missing_rate"
              :min="0"
              :max="1"
              :step="0.05" /></el-form-item
          ><el-form-item label="噪声率"
            ><el-input-number
              v-model="form.noise_rate"
              :min="0"
              :max="1"
              :step="0.05" /></el-form-item
          ><el-form-item label="异常率"
            ><el-input-number
              v-model="form.extreme_rate"
              :min="0"
              :max="1"
              :step="0.05" /></el-form-item></el-form
      ></template>
      <p class="muted">
        Seed
        不同仍可能生成相同数据，平台会检查实际数据哈希。盲测不更新赢家。缺失场景评价当前实际参与标签的统计量。
      </p>
      <el-button type="primary" :loading="busy" :disabled="active || !ids.length" @click="run"
        >冻结配置并运行评测</el-button
      >
    </section>
  </div>
  <JobPanel v-if="id" :id="id" />
  <section v-if="job?.kind === 'statistics_pipeline' && job.result" class="panel">
    <h2>统计候选流水线结果</h2>
    <JsonEvidence :value="job.result" /><RouterLink
      :to="{ path: '/experiments', query: { pipeline: String(job.result.run_id || '') } }"
      >查看统计流水线阶段与产物 →</RouterLink
    >
  </section>
  <section class="panel">
    <div class="panel-heading research-history-heading">
      <h2>历史评测与结果</h2>
      <el-select
        placeholder="选择已保存评测"
        aria-label="历史候选评测"
        @change="(value: string) => loadRun(value)"
        ><el-option
          v-for="item in history"
          :key="String(item.run_id)"
          :label="String(item.run_id).slice(0, 12) + ' · ' + statusLabel(String(item.status))"
          :value="String(item.run_id)"
      /></el-select>
    </div>
    <el-empty v-if="!report" description="选择历史评测，或提交新的冻结候选实验" :image-size="70" />
    <template v-else>
      <el-alert
        :title="
          independenceLabels[report.manifest.independence_status] ||
          report.manifest.independence_status
        "
        :type="
          report.audit.passed &&
          report.manifest.status === 'success' &&
          report.manifest.independence_status === 'verified_against_recorded_history'
            ? 'success'
            : 'warning'
        "
        :closable="false"
      />
      <p class="muted">
        当前结果任务：{{
          platform.tasks.find((task) => task.value === report?.manifest.config.task_profile)
            ?.title || report.manifest.config.task_profile
        }}；标签规模 {{ JSON.stringify(report.manifest.config.sizes) }}。 已完成
        {{ report.manifest.completed }} 个组合；文件审计{{
          report.audit.passed ? '通过' : '失败'
        }}。通过率区间为 Wilson 95% CI，样本少时区间较宽。成本为模拟通信 ms。
      </p>
      <el-radio-group v-model="split"
        ><el-radio-button value="blind">盲测</el-radio-button
        ><el-radio-button value="validation">独立验证</el-radio-button></el-radio-group
      >
      <MetricChart
        :labels="scenarios"
        :series="chartSeries"
        unit="模拟通信 ms"
        label="冻结候选跨场景通信成本对照"
      />
      <el-table :data="filteredRows"
        ><el-table-column prop="name" label="候选" min-width="160" /><el-table-column
          label="场景"
          min-width="200"
          ><template #default="{ row }"
            >{{ row.distribution }} / {{ row.num_tags }}
            <div class="path">{{ row.perturbation }}</div></template
          ></el-table-column
        ><el-table-column label="通过率 / 95% CI" min-width="160"
          ><template #default="{ row }"
            >{{ percent(row.pass_rate) }}
            <div class="muted">
              {{ percent(row.pass_ci95[0]) }} ～ {{ percent(row.pass_ci95[1]) }}
            </div></template
          ></el-table-column
        ><el-table-column label="模拟成本 / 降低比例" min-width="160"
          ><template #default="{ row }"
            >{{ format(row.mean_cost_ms) }} ms
            <div class="muted">相对基线 {{ percent(row.cost_reduction_fraction) }}</div></template
          ></el-table-column
        ><el-table-column label="比较资格" min-width="190"
          ><template #default="{ row }"
            ><el-tag v-if="row.is_pareto" type="success">同场景前沿</el-tag
            ><span v-else>{{
              row.eligible
                ? '可比较'
                : row.exclusion_reasons
                    .map((reason: string) => exclusionLabels[reason] || reason)
                    .join(' / ')
            }}</span></template
          ></el-table-column
        ></el-table
      >
      <div class="actions artifact-actions">
        <el-button
          v-for="asset in report.artifacts"
          :key="asset.path"
          link
          @click="preview = asset.path"
          >{{ assetLabel(asset.name) }}</el-button
        >
      </div>
      <JsonEvidence :value="report.manifest" title="冻结配置、源码哈希、来源和独立性证据" />
    </template>
  </section>
  <section v-if="preview" class="panel"><AssetPreview :path="preview" /></section>
</template>
