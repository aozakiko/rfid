<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { errorText, terminal, format, percent, statusLabel } from '../api'
import type { IterationResult } from '../types'
import TaskFields from '../components/TaskFields.vue'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import AssetPreview from '../components/AssetPreview.vue'
import StatusTag from '../components/StatusTag.vue'
import CandidateActions from '../components/CandidateActions.vue'
import MetricChart from '../components/MetricChart.vue'
const platform = usePlatform(),
  jobs = useJobs()
const form = reactive({
  parents: [] as string[],
  task_profile: 'mode_query',
  custom_task_text: '',
  prefix: 'web_vue_iter',
  iterations: 3,
  validate_num_tags: 80,
  max_repair_rounds: 1,
  dry_run: false,
  quality_selection: true,
  selection_objective: 'precision_constrained_cost',
  minimum_pass_rate: 1,
  evaluation_seed: 1000,
  evaluation_runs: 3,
  evaluation_price_min: 1,
  evaluation_price_max: 1000,
  relative_tolerance: 0.15,
  absolute_tolerance: 2,
  max_model_requests: 30,
  max_wall_seconds: 1800,
  no_improvement_rounds: 3,
  target_communication_cost_ms: 0,
  independent_validation: true,
  requirement_text: '面向 RFID 统计采集任务，优先满足完整语义，再控制通信开销。',
})
const id = ref(''),
  busy = ref(false),
  preview = ref('')
const job = computed(() => jobs.records[id.value])
const result = computed(() => job.value?.result as IterationResult | null)
const trend = computed(() => [
  {
    name: '子代模拟通信成本',
    data: (result.value?.iterations || []).map(
      (row) => row.evaluation?.mean_communication_cost_ms ?? null,
    ),
  },
])
const active = computed(() => job.value && !terminal(job.value.status))
const history = computed(() =>
  jobs.history.filter((row) => row.kind === 'auto_iterate').slice(0, 12),
)
watch(
  () => platform.scene,
  () => {
    form.parents = platform.protocols.slice(0, 4)
  },
  { immediate: true },
)
watch(
  () => form.task_profile,
  (value) => {
    form.prefix = 'web_' + value + '_iter'
  },
)
watch(
  () => job.value?.status,
  (status) => {
    if (status && terminal(status)) void platform.refresh().catch(() => {})
  },
)
async function run() {
  if (new Set(form.parents).size < 2) {
    ElMessage.error('至少选择两个不同父代')
    return
  }
  if (!form.dry_run && !platform.modelConfigured) {
    ElMessage.error('请先到协议设计页填写模型配置')
    return
  }
  if (form.task_profile === 'custom_task' && !form.custom_task_text.trim()) {
    ElMessage.error('请填写自定义任务说明')
    return
  }
  if (form.evaluation_price_max <= form.evaluation_price_min) {
    ElMessage.error('评价属性上限必须大于下限')
    return
  }
  busy.value = true
  try {
    id.value = (
      await jobs.submit('/api/selfopt/auto-iterate-async', {
        ...platform.model,
        ...form,
        scene: platform.scene,
      })
    ).id
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function retry(previousId: string) {
  try {
    id.value = (
      await jobs.submit('/api/selfopt/job/retry', { id: previousId, ...platform.model })
    ).id
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
function selectHistory(previousId: string) {
  id.value = previousId
  jobs.watch(previousId)
}
</script>
<template>
  <el-alert
    v-if="!platform.modelConfigured"
    title="真实迭代需要模型配置。可先运行预览，或到协议设计页填写 URL、Key 和模型。"
    type="info"
    :closable="false"
    class="space-bottom"
  />
  <div class="split">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>优化任务</h2>
          <p>当前场景 {{ platform.scene }}；候选验证结果决定父代更新。</p>
        </div>
        <el-tag>{{ platform.modelConfigured ? platform.model.model : '模型待配置' }}</el-tag>
      </div>
      <el-form label-position="top" class="form-grid"
        ><TaskFields v-model="form.task_profile" v-model:custom="form.custom_task_text" />
        <el-form-item label="父代协议池" class="full"
          ><el-select v-model="form.parents" multiple filterable aria-label="父代协议池"
            ><el-option v-for="name in platform.protocols" :key="name" :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="新协议前缀"
          ><el-input v-model="form.prefix" aria-label="新协议前缀" /></el-form-item
        ><el-form-item label="最大迭代轮数"
          ><el-input-number v-model="form.iterations" :min="1" :max="12"
        /></el-form-item>
        <el-form-item label="每候选验证标签数"
          ><el-input-number v-model="form.validate_num_tags" :min="20" :max="5000" /></el-form-item
        ><el-form-item label="最大修复轮数"
          ><el-input-number v-model="form.max_repair_rounds" :min="0" :max="5"
        /></el-form-item>
        <el-form-item label="选择目标" class="full"
          ><el-select v-model="form.selection_objective"
            ><el-option
              label="精度达标后降低模拟通信成本"
              value="precision_constrained_cost" /><el-option
              label="仅按任务质量选择"
              value="quality_only" /></el-select
        ></el-form-item>
        <el-form-item label="最低语义通过率"
          ><el-input-number
            v-model="form.minimum_pass_rate"
            :min="0"
            :max="1"
            :step="0.05" /></el-form-item
        ><el-form-item label="每候选评价次数"
          ><el-input-number v-model="form.evaluation_runs" :min="1" :max="20"
        /></el-form-item>
        <el-form-item label="开发集起始 Seed"
          ><el-input-number v-model="form.evaluation_seed" :min="0" /></el-form-item
        ><el-form-item label="相对误差门槛"
          ><el-input-number v-model="form.relative_tolerance" :min="0" :max="1" :step="0.05"
        /></el-form-item>
        <el-form-item label="属性下限"
          ><el-input-number v-model="form.evaluation_price_min" :min="1" /></el-form-item
        ><el-form-item label="属性上限"
          ><el-input-number v-model="form.evaluation_price_max" :min="2"
        /></el-form-item>
        <el-form-item label="需求与约束" class="full"
          ><el-input v-model="form.requirement_text" type="textarea" :rows="3"
        /></el-form-item>
      </el-form>
    </section>
    <div>
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h2>预算与停止条件</h2>
            <p>模型 HTTP 请求数包括修复、重试和接口回退。</p>
          </div>
        </div>
        <el-form label-position="top" class="form-grid"
          ><el-form-item label="模型请求数上限"
            ><el-input-number v-model="form.max_model_requests" :min="1" :max="500" /></el-form-item
          ><el-form-item label="运行时间预算（秒）"
            ><el-input-number v-model="form.max_wall_seconds" :min="1" :max="86400" /></el-form-item
          ><el-form-item label="连续无改进停止（0 关闭）"
            ><el-input-number
              v-model="form.no_improvement_rounds"
              :min="0"
              :max="12" /></el-form-item
          ><el-form-item label="目标模拟成本 ms（0 关闭）"
            ><el-input-number v-model="form.target_communication_cost_ms" :min="0" /></el-form-item
          ><el-form-item label="绝对误差门槛"
            ><el-input-number v-model="form.absolute_tolerance" :min="0" /></el-form-item
        ></el-form>
        <div><el-checkbox v-model="form.quality_selection">根据评价更新父代</el-checkbox></div>
        <div>
          <el-checkbox v-model="form.independent_validation"
            >搜索后冻结候选，自动独立验证</el-checkbox
          >
        </div>
        <div>
          <el-checkbox v-model="form.dry_run" aria-label="只预览任务"
            >只预览任务，不调用模型</el-checkbox
          >
        </div>
        <p class="muted">
          通信优化采用平台匿名软件信道；成本为参数模型下的模拟值。时间与停止在阶段边界检查，当前操作收尾可能跨过预算。
        </p>
        <div class="actions">
          <el-button
            type="primary"
            size="large"
            :loading="busy"
            :disabled="Boolean(active)"
            @click="run"
            >{{ form.dry_run ? '启动迭代预览' : '启动自动迭代' }}</el-button
          ><RouterLink to="/design"><el-button>模型配置</el-button></RouterLink>
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading">
          <h2>最近迭代任务</h2>
          <el-button link @click="jobs.refresh">刷新</el-button>
        </div>
        <el-empty v-if="!history.length" description="尚无迭代任务" :image-size="60" />
        <div v-for="item in history" :key="item.id" class="result-line">
          <el-button link @click="selectHistory(item.id)">{{
            item.payload.prefix || item.id.slice(0, 8)
          }}</el-button
          ><StatusTag :status="item.status" /><el-button
            v-if="['failed', 'cancelled', 'interrupted'].includes(item.status)"
            link
            @click="retry(item.id)"
            >新名称重试</el-button
          >
        </div>
      </section>
    </div>
  </div>
  <JobPanel v-if="id" :id="id" />
  <section v-if="result?.iterations" class="panel" data-testid="optimization-result">
    <div class="panel-heading">
      <h2>迭代结果</h2>
      <span class="pill">{{ result.dry_run ? '任务预览' : statusLabel(result.stop_reason) }}</span>
    </div>
    <div v-if="result.dry_run" class="muted space-bottom">
      只生成 Prompt，不调用模型或执行候选评价。
    </div>
    <template v-else
      ><div class="metric-grid">
        <div class="metric">
          <span>完成轮次</span><strong>{{ result.iterations.length }}</strong>
        </div>
        <div class="metric">
          <span>模型请求数</span
          ><strong
            >{{ result.budget?.model_requests }} / {{ result.budget?.max_model_requests }}</strong
          >
        </div>
        <div class="metric">
          <span>独立验证</span
          ><strong style="font-size: 18px">{{
            statusLabel(result.independent_validation?.status || 'disabled')
          }}</strong>
        </div>
        <div class="metric">
          <span>最终父代数</span><strong>{{ result.population?.length }}</strong>
        </div>
      </div>
      <p class="muted">最终父代池：{{ result.population?.join('，') }}</p>
      <p>
        停止原因：{{ statusLabel(result.stop_reason) }}；选择目标：{{
          result.selection_mode === 'precision_constrained_cost'
            ? '精度达标后降低模拟通信成本'
            : '任务质量探索'
        }}。
      </p>
      <p v-if="result.independent_validation">
        独立验证相对初始基线的模拟成本降低比例：{{
          percent(result.independent_validation.communication_reduction_fraction)
        }}。未满足比较条件时不报告收益。
      </p>
      <CandidateActions
        v-if="result.winner_candidate_id"
        :candidate-id="String(result.winner_candidate_id)"
        :baseline-id="
          result.baseline_candidate_id ? String(result.baseline_candidate_id) : undefined
        "
      />
      <MetricChart
        :labels="result.iterations.map((row) => '第 ' + row.iteration + ' 轮')"
        :series="trend"
        unit="模拟通信 ms"
        label="自动迭代子代通信成本趋势"
      />
      <el-alert
        v-if="result.independent_validation"
        :type="
          result.independent_validation.independent && result.independent_validation.winner_passed
            ? 'success'
            : 'warning'
        "
        :closable="false"
        :title="
          result.independent_validation.independent
            ? '冻结候选 ' +
              result.independent_validation.winner +
              '：' +
              (result.independent_validation.winner_passed ? '独立验证通过' : '独立验证未通过')
            : '尚未取得独立验证通过结论'
        "
        class="space-bottom"
      />
    </template>
    <el-table :data="result.iterations"
      ><el-table-column prop="iteration" label="轮次" width="65" /><el-table-column
        label="父代"
        min-width="190"
        ><template #default="{ row }"
          >{{ row.parent_a }} + {{ row.parent_b }}</template
        ></el-table-column
      ><el-table-column prop="protocol_name" label="子代" min-width="170" /><el-table-column
        label="状态"
        width="95"
        ><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column
      ><el-table-column label="评价 / 选择" min-width="160"
        ><template #default="{ row }"
          ><template v-if="row.evaluation"
            >{{ percent(row.evaluation.quality_pass_rate) }} /
            {{ format(row.evaluation.quality_score, 4) }}
            <div class="muted">
              {{ row.selection?.retained ? '进入父代池' : '未保留' }} · 模拟成本
              {{ format(row.evaluation.mean_communication_cost_ms) }} ms
            </div></template
          ><span v-else>—</span></template
        ></el-table-column
      ><el-table-column label="产物" width="130"
        ><template #default="{ row }"
          ><el-button link @click="preview = row.prompt_file || ''">Prompt</el-button
          ><el-button
            v-if="!result.dry_run && row.target_file"
            link
            @click="preview = row.target_file"
            >源码</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <div class="actions">
      <el-button v-if="result.evidence_path" @click="preview = result.evidence_path!"
        >查看运行证据</el-button
      ><el-button
        v-if="result.independent_validation?.evidence_path"
        @click="preview = result.independent_validation.evidence_path!"
        >查看独立验证</el-button
      >
    </div>
    <JsonEvidence :value="result" />
  </section>
  <section v-if="preview" class="panel"><AssetPreview :path="preview" /></section>
</template>
