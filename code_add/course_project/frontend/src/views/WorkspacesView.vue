<script setup lang="ts">
import { computed, reactive, ref, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { request, post, query, errorText, terminal, statusLabel, percent } from '../api'
import type { ResearchWorkspace, IterationResult, JsonObject, RegisteredCandidate } from '../types'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import SelectionEvidence from '../components/SelectionEvidence.vue'
import ModelSettings from '../components/ModelSettings.vue'
import StatusTag from '../components/StatusTag.vue'
const platform = usePlatform(),
  jobs = useJobs(),
  route = useRoute(),
  router = useRouter()
const tasks = ref<ResearchWorkspace[]>([]),
  task = ref<ResearchWorkspace | null>(null),
  selectedRunId = ref(''),
  busy = ref(false),
  savedSignature = ref(''),
  attachIds = ref<string[]>([]),
  registry = ref<RegisteredCandidate[]>([]),
  compareIds = ref<string[]>([])
const showArchived = ref(false)
const visibleTasks = computed(() =>
  tasks.value.filter((row) => showArchived.value || row.status === 'active'),
)
const roleLabels: Record<string, string> = {
  initial_parent: '初始父代',
  declared_baseline: '预设基线',
  winner: '演化赢家',
  run_baseline: '运行基线',
  evaluated: '已评估候选',
  attached: '关联候选',
}
const form = reactive({
  title: '',
  description: '',
  scene: '',
  task_profile: 'mean_estimation',
  optimization: {
    parents: [] as string[],
    baseline_protocol: '',
    selection_objective: 'pareto_diversity',
    iterations: 3,
    validate_num_tags: 80,
    evaluation_runs: 3,
    evaluation_seed: 1000,
    evaluation_price_min: 1,
    evaluation_price_max: 64,
    relative_tolerance: 0.15,
    absolute_tolerance: 2,
    minimum_pass_rate: 1,
    max_repair_rounds: 1,
    max_model_requests: 30,
    max_wall_seconds: 1800,
    no_improvement_rounds: 3,
    target_communication_cost_ms: 0,
    independent_validation: true,
    requirement_text: '',
  },
  distributions: ['multi_peak', 'uniform'],
  sizes: '80,200',
  validation_seeds: '1000001,1000002',
  blind_seeds: '2000001,2000002',
  robust: true,
})
const profiles = [
  'mode_query',
  'mean_estimation',
  'variance_estimation',
  'median_quantile',
  'cardinality_estimation',
  'top_k',
]
const actionLabels: Record<string, string> = {
  optimization: '自优化 / 预览',
  validation: '候选验证',
  evaluation: '跨场景评测',
}
const defaults = JSON.parse(JSON.stringify(form))
const savedPerturbations = ref<JsonObject[]>([])
let savedRobust = true
const names = computed(() => platform.data?.protocols[form.scene] || [])
const selectedRun = computed(() => task.value?.runs.find((row) => row.id === selectedRunId.value))
const job = computed(() =>
  selectedRun.value?.job_id
    ? jobs.records[selectedRun.value.job_id] || selectedRun.value.job
    : null,
)
const result = computed(() =>
  selectedRun.value?.action === 'optimization'
    ? (job.value?.result as IterationResult | null)
    : null,
)
const running = computed(
  () =>
    task.value?.runs.some(
      (row) => !terminal(jobs.records[row.job_id || '']?.status || row.status),
    ) || false,
)
const attachOptions = computed(() =>
  registry.value.filter(
    (row) =>
      row.supported_profiles.includes(form.task_profile) &&
      (row.family !== 'python' || row.scene === form.scene) &&
      !task.value?.candidates.some((item) => item.id === row.id),
  ),
)
const dirty = computed(() =>
  Boolean(task.value && savedSignature.value !== JSON.stringify(draft())),
)
let applying = false,
  loadSequence = 0
function publicModel() {
  const { api_key_value: _key, ...metadata } = platform.model
  return metadata.base_url && metadata.model ? metadata : task.value?.model_config || {}
}
function integers(text: string) {
  const parts = text.split(',').map((item) => item.trim())
  if (parts.some((item) => !/^\d+$/.test(item)))
    throw new Error('规模和 Seeds 需为逗号分隔的非负整数')
  return parts.map(Number)
}
function draft() {
  return {
    title: form.title,
    description: form.description,
    scene: form.scene,
    task_profile: form.task_profile,
    optimization: form.optimization,
    model_config: publicModel(),
    evaluation: {
      distributions: form.distributions,
      sizes: form.sizes,
      validation_seeds: form.validation_seeds,
      blind_seeds: form.blind_seeds,
      robust: form.robust,
    },
  }
}
async function list() {
  const data = await request<{ workspaces: ResearchWorkspace[] }>('/api/workspaces')
  tasks.value = data.workspaces
}
function apply(value: ResearchWorkspace) {
  applying = true
  if (!platform.model.base_url && value.model_config.base_url)
    Object.assign(platform.model, value.model_config)
  form.title = value.title
  form.description = value.description
  form.scene = value.scene
  form.task_profile = value.task_profile
  Object.assign(form.optimization, value.optimization)
  form.distributions = (value.evaluation.distributions as string[]) || ['multi_peak', 'uniform']
  form.sizes = ((value.evaluation.sizes as number[]) || [80, 200]).join(',')
  form.validation_seeds = (
    (value.evaluation.validation_seeds as number[]) || [1000001, 1000002]
  ).join(',')
  form.blind_seeds = ((value.evaluation.blind_seeds as number[]) || [2000001, 2000002]).join(',')
  form.robust = ((value.evaluation.perturbations as unknown[]) || [0, 1]).length > 1
  savedRobust = form.robust
  savedPerturbations.value = (value.evaluation.perturbations as JsonObject[]) || []
  task.value = value
  compareIds.value = []
  savedSignature.value = JSON.stringify({ ...draft(), model_config: value.model_config })
  void nextTick(() => {
    applying = false
  })
}
async function load(id: string, applyForm = true) {
  const sequence = ++loadSequence
  try {
    const response = await request<{ workspace: ResearchWorkspace }>(
      query('/api/workspaces', { id }),
    )
    if (sequence !== loadSequence) return
    if (applyForm) apply(response.workspace)
    else task.value = response.workspace
    for (const run of response.workspace.runs)
      if (run.job_id && run.job.id && Array.isArray(run.job.logs)) {
        jobs.records[run.job_id] = run.job
        if (!terminal(run.status)) jobs.watch(run.job_id)
      }
    if (!response.workspace.runs.some((row) => row.id === selectedRunId.value))
      selectedRunId.value = response.workspace.runs[0]?.id || ''
    await list()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
async function createNew() {
  loadSequence++
  task.value = null
  selectedRunId.value = ''
  savedSignature.value = ''
  compareIds.value = []
  Object.assign(form, JSON.parse(JSON.stringify(defaults)))
  savedPerturbations.value = []
  form.title = ''
  form.description = ''
  form.scene = platform.scene
  form.task_profile = 'mean_estimation'
  form.optimization.parents = names.value.slice(0, 2)
  form.optimization.baseline_protocol = form.optimization.parents[0] || ''
  await router.push({ name: 'workspaces' })
}
watch(
  () => form.scene,
  () => {
    if (!applying && !task.value) {
      form.optimization.parents = names.value.slice(0, 2)
      form.optimization.baseline_protocol = form.optimization.parents[0] || ''
    }
  },
)
watch(
  () => (route.name === 'workspaces' ? route.query.id : undefined),
  (id) => {
    if (id) void load(String(id))
  },
)
async function save(status?: 'active' | 'archived') {
  if (busy.value || running.value) return
  busy.value = true
  try {
    const payload = {
      ...draft(),
      id: task.value?.id,
      revision: task.value?.revision,
      status: status || task.value?.status || 'active',
      evaluation: {
        ...task.value?.evaluation,
        distributions: form.distributions,
        sizes: integers(form.sizes),
        validation_seeds: integers(form.validation_seeds),
        blind_seeds: integers(form.blind_seeds),
        perturbations:
          task.value && savedPerturbations.value.length && savedRobust === form.robust
            ? savedPerturbations.value
            : form.robust
              ? [
                  { missing_rate: 0, noise_rate: 0, extreme_rate: 0 },
                  { missing_rate: 0.1, noise_rate: 0.1, extreme_rate: 0.05 },
                ]
              : [{ missing_rate: 0, noise_rate: 0, extreme_rate: 0 }],
      },
    }
    const response = await post<{ workspace: ResearchWorkspace }>(
      task.value ? '/api/workspaces/update' : '/api/workspaces',
      payload,
    )
    apply(response.workspace)
    await list()
    await router.push({ name: 'workspaces', query: { id: response.workspace.id } })
    ElMessage.success('研究任务配置与初始源码版本已保存')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function run(action: string, preview = false, candidateId?: string) {
  if (!task.value || busy.value || running.value) return
  if (dirty.value) {
    ElMessage.warning('请先保存配置修改，再启动研究操作')
    return
  }
  if (action === 'optimization' && !preview && !platform.modelConfigured) {
    ElMessage.warning('请先在模型连接中填写本次模型配置与密钥，再保存模型元信息')
    return
  }
  busy.value = true
  try {
    const response = await post<{ job: import('../types').Job; workspace_run_id: string }>(
      '/api/workspaces/run',
      {
        id: task.value.id,
        revision: task.value.revision,
        action,
        preview,
        candidate_id: candidateId,
        candidate_ids:
          action === 'evaluation' && compareIds.value.length ? compareIds.value : undefined,
        model_config: action === 'optimization' && !preview ? platform.model : undefined,
      },
    )
    jobs.records[response.job.id] = response.job
    jobs.watch(response.job.id)
    selectedRunId.value = response.workspace_run_id
    await load(task.value.id, false)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function fetchRegistry() {
  try {
    registry.value = (
      await request<{ candidates: RegisteredCandidate[] }>('/api/research/candidates')
    ).candidates
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
async function attach() {
  if (!task.value || !attachIds.value.length) return
  try {
    const response = await post<{ workspace: ResearchWorkspace }>('/api/workspaces/attach', {
      id: task.value.id,
      revision: task.value.revision,
      candidate_ids: attachIds.value,
    })
    task.value = response.workspace
    attachIds.value = []
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
watch(
  () => job.value?.status,
  (status) => {
    if (status && terminal(status) && task.value) void load(task.value.id, false)
  },
)
onMounted(async () => {
  try {
    await list()
    if (route.query.id) await load(String(route.query.id))
    else await createNew()
    await fetchRegistry()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
})
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>研究任务工作区</h2>
        <p>保存任务配置、初始源码版本和每次运行的完整记录。API Key 仅在启动真实生成时提交。</p>
      </div>
      <div class="actions">
        <el-checkbox v-model="showArchived">显示已归档任务</el-checkbox
        ><el-button @click="createNew">新建研究任务</el-button>
      </div>
    </div>
    <el-table
      :data="visibleTasks"
      max-height="260"
      @row-click="
        (row: ResearchWorkspace) => router.push({ name: 'workspaces', query: { id: row.id } })
      "
      ><el-table-column prop="title" label="研究任务" min-width="200" /><el-table-column
        prop="scene"
        label="场景"
        min-width="130"
      /><el-table-column prop="task_profile" label="目标任务" min-width="160" /><el-table-column
        prop="revision"
        label="配置版本"
        width="90"
      /><el-table-column label="状态" width="100"
        ><template #default="{ row }">{{
          row.status === 'archived' ? '已归档' : '活动'
        }}</template></el-table-column
      ></el-table
    >
  </section>
  <el-alert
    v-if="dirty"
    title="存在尚未保存的配置修改；保存新版本后再运行。"
    type="warning"
    :closable="false"
    class="space-bottom"
  />
  <div class="split">
    <section class="panel">
      <div class="panel-heading">
        <h2>任务与演化配置</h2>
        <el-tag v-if="task">版本 {{ task.revision }}</el-tag>
      </div>
      <el-form label-position="top" class="form-grid" :disabled="running || busy">
        <el-form-item label="研究任务名称" class="full"
          ><el-input v-model="form.title" aria-label="研究任务名称" maxlength="120"
        /></el-form-item>
        <el-form-item label="研究说明" class="full"
          ><el-input v-model="form.description" type="textarea" :rows="2"
        /></el-form-item>
        <el-form-item label="任务场景"
          ><el-select v-model="form.scene" :disabled="Boolean(task)" aria-label="任务场景"
            ><el-option
              v-for="scene in platform.data?.scenes"
              :key="scene.name"
              :value="scene.name" /></el-select
        ></el-form-item>
        <el-form-item label="研究任务类型"
          ><el-select
            v-model="form.task_profile"
            :disabled="Boolean(task)"
            aria-label="研究任务类型"
            ><el-option
              v-for="profile in platform.tasks.filter((row) => profiles.includes(row.value))"
              :key="profile.value"
              :value="profile.value"
              :label="profile.title" /></el-select
        ></el-form-item>
        <el-form-item label="初始父代" class="full"
          ><el-select
            v-model="form.optimization.parents"
            multiple
            filterable
            aria-label="研究初始父代"
            ><el-option v-for="name in names" :key="name" :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="预设对照基线" class="full"
          ><el-select v-model="form.optimization.baseline_protocol"
            ><el-option
              v-for="name in form.optimization.parents"
              :key="name"
              :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="演化选择策略" class="full"
          ><el-select v-model="form.optimization.selection_objective" aria-label="演化选择策略"
            ><el-option value="pareto_diversity" label="Pareto 前沿与种群多样性" /><el-option
              value="precision_constrained_cost"
              label="精度达标后降低模拟通信成本" /><el-option
              value="quality_only"
              label="任务质量探索" /></el-select
        ></el-form-item>
        <el-form-item label="最大演化轮数"
          ><el-input-number v-model="form.optimization.iterations" :min="1" :max="12"
        /></el-form-item>
        <el-form-item label="最大修复轮数"
          ><el-input-number v-model="form.optimization.max_repair_rounds" :min="0" :max="5"
        /></el-form-item>
        <el-form-item label="开发集标签数"
          ><el-input-number v-model="form.optimization.validate_num_tags" :min="20" :max="5000"
        /></el-form-item>
        <el-form-item label="开发集重复次数"
          ><el-input-number v-model="form.optimization.evaluation_runs" :min="1" :max="20"
        /></el-form-item>
        <el-form-item label="开发集 Seed"
          ><el-input-number v-model="form.optimization.evaluation_seed" :min="0"
        /></el-form-item>
        <el-form-item label="最低语义通过率"
          ><el-input-number
            v-model="form.optimization.minimum_pass_rate"
            :min="0"
            :max="1"
            :step="0.05"
        /></el-form-item>
        <el-form-item label="属性下限"
          ><el-input-number v-model="form.optimization.evaluation_price_min" :min="1"
        /></el-form-item>
        <el-form-item label="属性上限"
          ><el-input-number v-model="form.optimization.evaluation_price_max" :min="3"
        /></el-form-item>
        <el-form-item label="相对误差门槛"
          ><el-input-number
            v-model="form.optimization.relative_tolerance"
            :min="0"
            :max="1"
            :step="0.05"
        /></el-form-item>
        <el-form-item label="绝对误差门槛"
          ><el-input-number v-model="form.optimization.absolute_tolerance" :min="0"
        /></el-form-item>
        <el-form-item label="模型请求预算"
          ><el-input-number v-model="form.optimization.max_model_requests" :min="1" :max="500"
        /></el-form-item>
        <el-form-item label="时间预算（秒）"
          ><el-input-number v-model="form.optimization.max_wall_seconds" :min="1" :max="86400"
        /></el-form-item>
        <el-form-item label="连续无改进停止轮数"
          ><el-input-number v-model="form.optimization.no_improvement_rounds" :min="0" :max="12"
        /></el-form-item>
        <el-form-item label="目标模拟通信成本"
          ><el-input-number v-model="form.optimization.target_communication_cost_ms" :min="0"
        /></el-form-item>
        <el-form-item label="生成需求与约束" class="full"
          ><el-input v-model="form.optimization.requirement_text" type="textarea" :rows="3"
        /></el-form-item>
        <el-checkbox v-model="form.optimization.independent_validation"
          >搜索后独立验证冻结赢家</el-checkbox
        >
      </el-form>
    </section>
    <section class="panel">
      <div class="panel-heading"><h2>评测计划与版本信息</h2></div>
      <el-form label-position="top" class="form-grid" :disabled="running || busy"
        ><el-form-item label="评测分布" class="full"
          ><el-select v-model="form.distributions" multiple
            ><el-option
              v-for="distribution in [
                'single_peak',
                'multi_peak',
                'uniform',
                'gaussian',
                'long_tail',
                'power_law',
              ]"
              :key="distribution"
              :value="distribution" /></el-select
        ></el-form-item>
        <el-form-item label="评测标签规模" class="full"
          ><el-input v-model="form.sizes" aria-label="评测标签规模"
        /></el-form-item>
        <el-form-item label="验证 Seeds"
          ><el-input v-model="form.validation_seeds" aria-label="研究验证 Seeds" /></el-form-item
        ><el-form-item label="盲测 Seeds"
          ><el-input v-model="form.blind_seeds" aria-label="研究盲测 Seeds"
        /></el-form-item>
        <el-checkbox v-model="form.robust">加入缺失、噪声与异常扰动</el-checkbox>
      </el-form>
      <p class="muted">
        评测使用冻结候选，验证与盲测检查实际数据重叠。不同评测引擎保持各自的比较边界。
      </p>
      <h3>模型配置</h3>
      <p>
        {{ publicModel().model || '尚未保存模型' }} ·
        {{ publicModel().base_url || '可先进行任务预览与基线验证' }}
      </p>
      <el-collapse
        ><el-collapse-item title="配置本次模型连接"><ModelSettings /></el-collapse-item
      ></el-collapse>
      <p class="muted">
        仅保存模型身份与采样参数；修改后需保存新的配置版本。刷新页面后真实生成需重新填写密钥。
      </p>
      <template v-if="task"
        ><h3>冻结输入</h3>
        <div class="path">Task SHA256：{{ task.task_source_sha256 }}</div>
        <p class="muted">
          场景和任务类型在本研究任务内固定。父代源码变化时需显式保存新版本，旧运行仍对应原配置。
        </p>
        <div class="actions">
          <a :href="query('/api/workspaces/export', { id: task.id })">下载研究任务记录</a
          ><a :href="query('/api/selfopt/download', { path: task.configuration_path })"
            >下载当前配置快照</a
          >
        </div></template
      >
      <div class="actions">
        <el-button type="primary" :loading="busy" :disabled="running" @click="save()">{{
          task ? '保存配置新版本' : '保存研究任务'
        }}</el-button
        ><el-button
          v-if="task"
          :disabled="running || busy"
          @click="save(task.status === 'archived' ? 'active' : 'archived')"
          >{{ task.status === 'archived' ? '恢复任务' : '归档任务' }}</el-button
        >
      </div>
      <div v-if="task" class="actions">
        <el-button
          :disabled="running || busy || dirty || task.status === 'archived'"
          @click="run('optimization', true)"
          >预览任务</el-button
        ><el-button
          type="primary"
          :disabled="running || busy || dirty || task.status === 'archived'"
          @click="run('optimization')"
          >启动研究自优化</el-button
        ><el-button
          :disabled="running || busy || dirty || task.status === 'archived'"
          @click="run('validation')"
          >验证当前赢家或基线</el-button
        ><el-button
          :disabled="running || busy || dirty || task.status === 'archived'"
          @click="run('evaluation')"
          >运行研究跨场景评测</el-button
        >
      </div>
    </section>
  </div>
  <section v-if="task" class="panel">
    <div class="panel-heading">
      <h2>研究候选版本</h2>
      <el-button @click="fetchRegistry">刷新可关联候选</el-button>
    </div>
    <el-alert
      v-if="task.candidate_problems.length"
      title="部分候选完整性校验失败，请查看冻结资产。"
      type="error"
      :closable="false"
    />
    <el-form label-position="top"
      ><el-form-item label="关联已有候选版本"
        ><el-select v-model="attachIds" multiple filterable aria-label="关联已有候选版本"
          ><el-option
            v-for="row in attachOptions"
            :key="row.id"
            :value="row.id"
            :label="
              row.name + ' · ' + row.family + ' · ' + row.id.slice(0, 8)
            " /></el-select></el-form-item
      ><el-button :disabled="!attachIds.length || running" @click="attach"
        >关联到研究任务</el-button
      ></el-form
    >
    <el-table
      :data="task.candidates"
      @selection-change="(rows: RegisteredCandidate[]) => (compareIds = rows.map((row) => row.id))"
      ><el-table-column type="selection" width="45" /><el-table-column
        label="冻结候选"
        min-width="230"
        ><template #default="{ row }"
          >{{ row.name }}
          <div class="path">{{ row.id.slice(0, 12) }} · {{ row.family }}</div></template
        ></el-table-column
      ><el-table-column label="角色" min-width="180"
        ><template #default="{ row }">{{
          row.roles.map((role: string) => roleLabels[role] || role).join(' / ')
        }}</template></el-table-column
      ><el-table-column label="后续操作" min-width="190"
        ><template #default="{ row }"
          ><el-button
            v-if="row.family === 'python'"
            :disabled="running || dirty"
            @click="run('validation', false, row.id)"
            >验证冻结版本</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <p class="muted">
      勾选候选后比较所选版本，Python
      评测自动加入预设基线；未勾选时比较当前配置版本的赢家与基线。Python 与 JSON 请分开选择，JSON
      评测每次选择一个版本和一组扰动。
    </p>
  </section>
  <section v-if="task" class="panel">
    <div class="panel-heading">
      <h2>研究运行记录</h2>
      <el-button @click="load(task!.id, false)">刷新研究记录</el-button>
    </div>
    <el-table
      :data="task.runs"
      @row-click="(row: ResearchWorkspace['runs'][number]) => (selectedRunId = row.id)"
      ><el-table-column prop="created_at" label="时间" min-width="180" /><el-table-column
        prop="revision"
        label="配置版本"
        width="90"
      /><el-table-column label="操作" min-width="150"
        ><template #default="{ row }">{{
          actionLabels[row.action] || row.action
        }}</template></el-table-column
      ><el-table-column label="状态" width="100"
        ><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column
      ><el-table-column label="运行 ID" min-width="230"
        ><template #default="{ row }"
          ><span class="path">{{ row.id }}</span></template
        ></el-table-column
      ></el-table
    >
  </section>
  <JobPanel v-if="selectedRun?.job_id && job" :id="selectedRun.job_id" />
  <section v-if="selectedRun" class="panel">
    <JsonEvidence :value="selectedRun.config" title="本次运行冻结配置" /><JsonEvidence
      v-if="!selectedRun.job_id"
      :value="selectedRun.job"
      title="任务提交失败详情"
    />
  </section>
  <section v-if="task?.revisions?.length" class="panel">
    <h2>任务配置版本</h2>
    <el-collapse
      ><el-collapse-item
        v-for="revision in task.revisions"
        :key="revision.revision"
        :title="'版本 ' + revision.revision + ' · ' + revision.created_at"
        ><JsonEvidence :value="revision.configuration" title="配置与源码摘要" /></el-collapse-item
    ></el-collapse>
  </section>
  <section v-if="job?.result" class="panel">
    <h2>本次研究结果</h2>
    <p v-if="result?.stop_reason">
      停止原因：{{ statusLabel(result.stop_reason) }}；独立验证模拟成本降低比例：{{
        percent(result.independent_validation?.communication_reduction_fraction)
      }}
    </p>
    <SelectionEvidence v-if="result?.final_selection" :report="result.final_selection" />
    <el-collapse v-if="result?.iterations"
      ><el-collapse-item
        v-for="round in result.iterations.filter((item) => item.selection_details)"
        :key="round.iteration"
        :title="'第 ' + round.iteration + ' 轮选择原因'"
        ><SelectionEvidence
          v-if="round.selection_details"
          :report="round.selection_details" /></el-collapse-item
    ></el-collapse>
    <RouterLink
      v-if="selectedRun?.action === 'evaluation' && job.result.run_id"
      :to="
        job.kind === 'statistics_pipeline'
          ? { path: '/experiments', query: { pipeline: String(job.result.run_id) } }
          : { path: '/research', query: { run: String(job.result.run_id) } }
      "
      >打开完整评测结果与证据 →</RouterLink
    >
    <JsonEvidence :value="job.result" title="运行结果与原始证据" />
  </section>
</template>
