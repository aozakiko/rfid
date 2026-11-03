<script setup lang="ts">
import { computed, reactive, ref, watch, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { request, query, format, percent, errorText, terminal } from '../api'
import type { ValidationReport, JsonObject, RegisteredCandidate } from '../types'
import TaskFields from '../components/TaskFields.vue'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import StatusTag from '../components/StatusTag.vue'
import CandidateActions from '../components/CandidateActions.vue'
const route = useRoute()
const platform = usePlatform(),
  jobs = useJobs()
const form = reactive({
  protocol: '',
  task_profile: 'mode_query',
  custom_task_text: '',
  security_mode: 'strict',
  seed: 0,
  num_tags: 120,
  total_runs: 3,
  price_min: 1,
  price_max: 1000,
  relative_tolerance: 0.15,
  absolute_tolerance: 2,
  quantile: 0.5,
})
const report = ref<ValidationReport | null>(null),
  busy = ref(false),
  history = ref<JsonObject[]>([]),
  id = ref(''),
  validationId = ref(''),
  frozen = ref<RegisteredCandidate | null>(null),
  compareNames = ref<string[]>([])
const validationJob = computed(() => jobs.records[validationId.value])
const validationActive = computed(() =>
  Boolean(validationId.value && !terminal(validationJob.value?.status || 'running')),
)
const comparison = computed(() => jobs.records[id.value]?.result)
const comparisonRows = computed(() => (comparison.value?.results as JsonObject[]) || [])
let applyingCandidate = false
watch(
  [() => platform.scene, () => form.protocol, () => form.task_profile],
  () => {
    if (!applyingCandidate) frozen.value = null
  },
  { flush: 'sync' },
)
watch(
  () => platform.scene,
  () => {
    form.protocol = platform.protocols[0] || ''
    compareNames.value = platform.protocols.slice(0, 2)
    void loadHistory()
  },
  { immediate: true },
)
async function loadHistory() {
  try {
    history.value = (
      await request<{ reports: JsonObject[] }>(
        query('/api/selfopt/validation-reports', { scene: platform.scene, limit: 20 }),
      )
    ).reports
  } catch {
    /* The form remains available while history is offline. */
  }
}
async function validate() {
  if (busy.value || validationActive.value) return
  if (!form.protocol) {
    ElMessage.error('请选择协议')
    return
  }
  if (form.price_max <= form.price_min) {
    ElMessage.error('属性上限必须大于下限')
    return
  }
  busy.value = true
  try {
    report.value = null
    validationId.value = (
      await jobs.submit('/api/selfopt/validate-async', {
        ...form,
        scene: platform.scene,
        candidate_id: frozen.value?.id,
      })
    ).id
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
watch(
  () => validationJob.value?.status,
  async (status) => {
    if (status === 'success' && validationJob.value?.result) {
      report.value = validationJob.value.result as ValidationReport
      await loadHistory()
    }
  },
)
async function applyCandidate() {
  if (route.name !== 'validation' || !route.query.candidate) return
  try {
    const target = route.query.candidate
    const result = await request<{ candidates: RegisteredCandidate[] }>('/api/research/candidates')
    const selected = result.candidates.find((row) => row.id === target)
    if (route.name !== 'validation' || route.query.candidate !== target) return
    if (!selected || selected.family !== 'python')
      throw new Error('该冻结 Python 候选不存在或完整性校验失败，请刷新候选版本库')
    applyingCandidate = true
    platform.scene = selected.scene
    await nextTick()
    form.protocol = selected.name
    form.task_profile = selected.task_profile
    form.security_mode = 'strict'
    frozen.value = selected
    report.value = null
    await nextTick()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    applyingCandidate = false
  }
}
watch(
  () => (route.name === 'validation' ? route.query.candidate : undefined),
  () => {
    void applyCandidate()
  },
)
async function compare() {
  if (!compareNames.value.length) {
    ElMessage.error('至少选择一个协议')
    return
  }
  try {
    id.value = (
      await jobs.submit('/api/selfopt/batch-test-async', {
        ...form,
        scene: platform.scene,
        protocols: compareNames.value,
      })
    ).id
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
async function detail(row: JsonObject) {
  try {
    report.value = await request<ValidationReport>(
      query('/api/selfopt/validation-report', { id: Number(row.id) }),
    )
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
onMounted(() => {
  void loadHistory()
  void applyCandidate()
})
</script>
<template>
  <div class="split">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>验证配置</h2>
          <p>真值和语义判定来自平台，通信自报值只用于诊断。</p>
        </div>
      </div>
      <el-form label-position="top" class="form-grid"
        ><TaskFields v-model="form.task_profile" v-model:custom="form.custom_task_text" />
        <el-form-item label="协议" class="full"
          ><el-select v-model="form.protocol" filterable aria-label="待验证协议"
            ><el-option v-for="name in platform.protocols" :key="name" :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="隔离模式"
          ><el-select v-model="form.security_mode"
            ><el-option label="严格隔离" value="strict" /><el-option
              label="历史兼容（告警）"
              value="legacy" /></el-select
        ></el-form-item>
        <el-form-item label="起始 Seed"
          ><el-input-number v-model="form.seed" :min="0" /></el-form-item
        ><el-form-item label="标签数量"
          ><el-input-number v-model="form.num_tags" :min="20" :max="200000" /></el-form-item
        ><el-form-item label="重复次数"
          ><el-input-number v-model="form.total_runs" :min="1" :max="200"
        /></el-form-item>
        <el-form-item label="属性下限"
          ><el-input-number v-model="form.price_min" :min="1" /></el-form-item
        ><el-form-item label="属性上限"
          ><el-input-number v-model="form.price_max" :min="2"
        /></el-form-item>
        <el-form-item label="相对误差门槛"
          ><el-input-number
            v-model="form.relative_tolerance"
            :min="0"
            :max="1"
            :step="0.05" /></el-form-item
        ><el-form-item label="绝对误差门槛"
          ><el-input-number v-model="form.absolute_tolerance" :min="0"
        /></el-form-item>
        <el-form-item v-if="form.task_profile === 'median_quantile'" label="目标分位数"
          ><el-input-number v-model="form.quantile" :min="0" :max="1" :step="0.05"
        /></el-form-item> </el-form
      ><el-alert
        v-if="frozen"
        :title="
          '使用冻结版本 ' + frozen.id.slice(0, 12) + '；修改协议、场景或任务将恢复为当前源码验证。'
        "
        type="info"
        :closable="false"
        class="space-bottom"
      />
      <el-button type="primary" :loading="busy" :disabled="validationActive" @click="validate"
        >验证协议</el-button
      >
    </section>
    <section class="panel">
      <div class="panel-heading">
        <h2>验证结果</h2>
        <StatusTag v-if="report" :status="report.final_decision" />
      </div>
      <el-empty v-if="!report" description="提交验证后查看阶段判定和逐次结果" :image-size="90" />
      <template v-else>
        <div class="metric-grid" style="grid-template-columns: 1fr 1fr">
          <div class="metric">
            <span>语义通过率</span
            ><strong>{{
              report.semantic_pass_rate === null ? '—' : format(report.semantic_pass_rate) + '%'
            }}</strong
            ><small>按配置的完整输出语义</small>
          </div>
          <div class="metric">
            <span>已完成执行</span
            ><strong>{{ report.completed_runs }} / {{ report.total_runs }}</strong
            ><small>失败结果保留在证据中</small>
          </div>
        </div>
        <div v-for="stage in report.validation_stages" :key="stage.id" class="result-line">
          <span>{{ stage.label }}</span
          ><StatusTag :status="stage.status" />
        </div>
        <div class="path">源码 SHA256：{{ report.source_sha256 }}</div>
        <p v-if="report.final_decision !== 'passed'" class="muted">
          未通过或不可用的阶段：{{
            report.validation_stages
              .filter((stage) => stage.status !== 'passed')
              .map((stage) => stage.label)
              .join('、')
          }}。请展开原始证据查看具体原因，再修改协议重新验证。
        </p>
        <CandidateActions
          v-if="
            report.candidate_id ||
            (report.protocol_path && !String(report.protocol_path).includes('/outputs/research/'))
          "
          :candidate-id="report.candidate_id ? String(report.candidate_id) : undefined"
          :path="String(report.protocol_path || '')"
          :scene="String(report.scene || platform.scene)"
          :profile="String(report.task_profile || form.task_profile)"
          :protocol="report.protocol"
        />
        <el-table :data="report.runs" max-height="300"
          ><el-table-column prop="seed" label="Seed" width="75" /><el-table-column
            label="估计 / 真值"
            min-width="150"
            ><template #default="{ row }"
              >{{ JSON.stringify(row.estimated_values) }}
              <div class="muted">真值 {{ JSON.stringify(row.target_values) }}</div></template
            ></el-table-column
          ><el-table-column label="语义" width="80"
            ><template #default="{ row }">{{
              row.semantic_hit === null ? '不可用' : row.semantic_hit ? '通过' : '未通过'
            }}</template></el-table-column
          ><el-table-column prop="cost_origin" label="成本来源" min-width="140"
        /></el-table>
        <JsonEvidence :value="report" />
      </template>
    </section>
  </div>
  <JobPanel v-if="validationId" :id="validationId" />
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>同场景候选比较</h2>
        <p>使用上方任务、种子和精度配置，自报耗时不参与质量排名。</p>
      </div>
    </div>
    <el-select v-model="compareNames" multiple filterable aria-label="候选比较协议"
      ><el-option v-for="name in platform.protocols" :key="name" :value="name"
    /></el-select>
    <div class="actions">
      <el-button
        :disabled="Boolean(id && !terminal(jobs.records[id]?.status || 'running'))"
        @click="compare"
        >开始比较</el-button
      >
    </div>
    <el-table v-if="comparisonRows.length" :data="comparisonRows"
      ><el-table-column prop="name" label="协议" min-width="160" /><el-table-column
        prop="rank"
        label="排名"
        width="65" /><el-table-column label="通过率"
        ><template #default="{ row }">{{
          percent(row.quality_pass_rate)
        }}</template></el-table-column
      ><el-table-column label="质量分"
        ><template #default="{ row }">{{ format(row.quality_score, 4) }}</template></el-table-column
      ><el-table-column prop="cost_scope" label="成本口径" min-width="180" /></el-table
    ><JsonEvidence v-if="comparison" :value="comparison" />
  </section>
  <JobPanel v-if="id" :id="id" />
  <section class="panel">
    <div class="panel-heading">
      <h2>验证历史</h2>
      <el-button @click="loadHistory">刷新记录</el-button>
    </div>
    <el-table :data="history" @row-click="detail"
      ><el-table-column prop="created_at" label="时间" min-width="150" /><el-table-column
        prop="protocol"
        label="协议"
        min-width="160" /><el-table-column
        prop="task_profile"
        label="任务"
        min-width="140" /><el-table-column prop="security_mode" label="模式" /><el-table-column
        label="判定"
        ><template #default="{ row }"
          ><StatusTag :status="String(row.final_decision)" /></template></el-table-column
    ></el-table>
  </section>
</template>
