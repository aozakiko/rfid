<script setup lang="ts">
import { computed, reactive, ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { post, request, query, errorText, terminal } from '../api'
import type { EvidenceRun, JsonObject } from '../types'
import TaskFields from '../components/TaskFields.vue'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import AssetPreview from '../components/AssetPreview.vue'
const platform = usePlatform(),
  jobs = useJobs()
const route = useRoute()
const config = reactive({
  name: 'web-vue-experiment',
  profiles: ['mean_estimation', 'variance_estimation', 'cardinality_estimation', 'top_k'],
  protocols: 'shared_sketch,adaptive',
  seeds: '10,11,12',
  sizes: '1000',
  distributions: ['multi_peak'],
  split: 'development',
  price_min: 1,
  price_max: 64,
  missing_rate: 0,
  noise_rate: 0,
  extreme_rate: 0,
})
const validationSeeds = ref('100,101,102'),
  blindSeeds = ref('200,201,202'),
  id = ref(''),
  record = ref<EvidenceRun | null>(null),
  pipeline = ref<EvidenceRun | null>(null),
  preview = ref(''),
  busy = ref(false)
const classic = reactive({
  protocol: '',
  task_profile: 'mode_query',
  custom_task_text: '',
  factors: ['density'],
  seed: 2000,
  num_tags: 2000,
  total_runs: 3,
  point_count: 3,
  price_min: 1,
  price_max: 1000,
  density_max: 10000,
  security_mode: 'strict',
  output: 'Experiments/sensitivity_results_vue.csv',
  output_dir: 'Experiments',
  charts: 'accuracy',
})
const classicResult = ref<JsonObject | null>(null)
const job = computed(() => jobs.records[id.value])
const taskActive = computed(() => Boolean(job.value && !terminal(job.value.status)))
watch(
  () => platform.scene,
  () => {
    classic.protocol = platform.protocols[0] || ''
  },
  { immediate: true },
)
watch(
  () => job.value?.status,
  (status) => {
    if (status === 'success') {
      void load()
      void platform.refresh().catch(() => {})
    }
  },
)
function list(value: string, numeric = false) {
  const parts = value.split(',').map((s) => s.trim())
  if (parts.some((s) => !s)) throw new Error('逗号分隔配置含空项')
  if (!numeric) return parts
  const values = parts.map(Number)
  if (values.some((n) => !Number.isSafeInteger(n) || n < 0))
    throw new Error('种子和规模需填写非负整数')
  return values
}
async function load(runId = '', pipelineId = '') {
  try {
    ;[record.value, pipeline.value] = await Promise.all([
      request<EvidenceRun>(query('/api/statistics/config-runs', { run_id: runId })),
      request<EvidenceRun>(query('/api/statistics/pipelines', { run_id: pipelineId })),
    ])
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
async function submit(kind: 'config' | 'pipeline' | 'rebuild') {
  if (busy.value || taskActive.value) return
  busy.value = true
  try {
    const body = {
      ...config,
      protocols: list(config.protocols),
      seeds: list(config.seeds, true),
      sizes: list(config.sizes, true),
    }
    if (body.price_max <= body.price_min) throw new Error('属性上限必须大于下限')
    const payload =
      kind === 'rebuild'
        ? { run_id: record.value?.run_id }
        : kind === 'pipeline'
          ? {
              config: body,
              validation_seeds: list(validationSeeds.value, true),
              blind_seeds: list(blindSeeds.value, true),
            }
          : { config: body }
    id.value = (
      await jobs.submit(
        '/api/statistics/' +
          (kind === 'config' ? 'run-config' : kind === 'pipeline' ? 'run-pipeline' : 'rebuild'),
        payload,
      )
    ).id
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function runClassic(task: string) {
  if (busy.value || taskActive.value) return
  busy.value = true
  try {
    id.value = (
      await jobs.submit('/api/selfopt/experiments-async', {
        ...classic,
        task,
        source_scene: platform.scene,
        scenario: platform.scene,
        protocols: classic.protocol + ':' + platform.scene,
        factors: classic.factors.join(','),
        csv: classic.output,
      })
    ).id
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
watch(
  () => (route.name === 'experiments' ? route.query.pipeline : undefined),
  (value) => {
    if (value) void load('', String(value))
  },
)
onMounted(() => load('', String(route.query.pipeline || '')))
watch(
  () => job.value?.status,
  (status) => {
    if (status === 'success' && job.value?.kind === 'scene_experiment')
      classicResult.value = job.value.result
  },
)
</script>
<template>
  <el-tabs>
    <el-tab-pane label="可复现实验与盲测流水线">
      <div class="split">
        <section class="panel">
          <div class="panel-heading">
            <h2>实验配置</h2>
            <el-tag>无需模型密钥</el-tag>
          </div>
          <el-form label-position="top" class="form-grid"
            ><el-form-item label="实验名称" class="full"
              ><el-input v-model="config.name" /></el-form-item
            ><el-form-item label="任务画像" class="full"
              ><el-select v-model="config.profiles" multiple
                ><el-option
                  v-for="task in platform.tasks.filter(
                    (t) =>
                      ![
                        'custom_task',
                        'missing_stocktaking',
                        'mode_query',
                        'median_quantile',
                      ].includes(t.value),
                  )"
                  :key="task.value"
                  :label="task.title"
                  :value="task.value" /></el-select
            ></el-form-item>
            <el-form-item label="协议（逗号分隔）" class="full"
              ><el-input v-model="config.protocols" /></el-form-item
            ><el-form-item label="开发 Seeds"><el-input v-model="config.seeds" /></el-form-item
            ><el-form-item label="标签规模"><el-input v-model="config.sizes" /></el-form-item>
            <el-form-item label="分布" class="full"
              ><el-select v-model="config.distributions" multiple
                ><el-option
                  v-for="name in [
                    'single_peak',
                    'multi_peak',
                    'uniform',
                    'gaussian',
                    'long_tail',
                    'power_law',
                  ]"
                  :key="name"
                  :value="name" /></el-select
            ></el-form-item>
            <el-form-item label="属性下限"
              ><el-input-number v-model="config.price_min" :min="0" /></el-form-item
            ><el-form-item label="属性上限"
              ><el-input-number v-model="config.price_max" :min="1"
            /></el-form-item>
            <el-form-item label="缺失率"
              ><el-input-number
                v-model="config.missing_rate"
                :min="0"
                :max="1"
                :step="0.05" /></el-form-item
            ><el-form-item label="噪声率"
              ><el-input-number
                v-model="config.noise_rate"
                :min="0"
                :max="1"
                :step="0.05" /></el-form-item
            ><el-form-item label="异常率"
              ><el-input-number
                v-model="config.extreme_rate"
                :min="0"
                :max="1"
                :step="0.05" /></el-form-item
            ><el-form-item label="数据划分"
              ><el-select v-model="config.split"
                ><el-option
                  v-for="value in ['development', 'validation', 'blind']"
                  :key="value"
                  :value="value" /></el-select
            ></el-form-item>
            <el-form-item label="流水线验证 Seeds"
              ><el-input v-model="validationSeeds" /></el-form-item
            ><el-form-item label="流水线盲测 Seeds"><el-input v-model="blindSeeds" /></el-form-item>
          </el-form>
          <div class="actions">
            <el-button
              type="primary"
              :loading="busy"
              :disabled="taskActive"
              @click="submit('config')"
              >运行配置实验</el-button
            ><el-button :loading="busy" :disabled="taskActive" @click="submit('pipeline')"
              >运行完整盲测流水线</el-button
            >
          </div>
        </section>
        <section class="panel">
          <div class="panel-heading">
            <h2>运行证据</h2>
            <el-button @click="load()">刷新</el-button>
          </div>
          <el-select
            :model-value="record?.run_id"
            placeholder="选择实验运行"
            @change="(value: string) => load(value)"
            ><el-option v-for="item in record?.runs" :key="item.run_id" :value="item.run_id"
          /></el-select>
          <div v-if="record?.available">
            <p>
              <el-tag :type="record.audit?.passed ? 'success' : 'danger'"
                >产物审计{{ record.audit?.passed ? '通过' : '未通过' }}</el-tag
              >
            </p>
            <div class="path">{{ record.run_id }}</div>
            <el-button
              :disabled="!record.audit?.passed || taskActive || busy"
              @click="submit('rebuild')"
              >从原始结果重建报告</el-button
            ><JsonEvidence
              :value="record.manifest"
              title="冻结配置、TaskSpec 和复现身份"
            /><JsonEvidence :value="record.rows" title="统计结果、置信区间和排除原因" />
          </div>
          <el-empty v-else description="尚无配置化运行" :image-size="60" />
          <h3>盲测流水线</h3>
          <el-select
            :model-value="pipeline?.run_id"
            placeholder="选择流水线"
            @change="(value: string) => load('', value)"
            ><el-option
              v-for="item in pipeline?.runs"
              :key="item.run_id"
              :value="item.run_id" /></el-select
          ><JsonEvidence
            v-if="pipeline?.available"
            :value="{ manifest: pipeline.manifest, audit: pipeline.audit }"
            title="流水线阶段、数据划分与复现核验"
          />
          <div class="actions">
            <el-button
              v-for="item in [...(record?.artifacts || []), ...(pipeline?.artifacts || [])].slice(
                0,
                20,
              )"
              :key="item.path"
              link
              @click="preview = item.path"
              >{{ item.name }}</el-button
            >
          </div>
        </section>
      </div>
    </el-tab-pane>
    <el-tab-pane label="敏感度与演化图">
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h2>场景实验</h2>
            <p>共享验证沙箱计量；缺少候选来源时不推断谱系或修复次数。</p>
          </div>
        </div>
        <el-form label-position="top" class="form-grid"
          ><TaskFields
            v-model="classic.task_profile"
            v-model:custom="classic.custom_task_text" /><el-form-item label="协议"
            ><el-select v-model="classic.protocol" filterable
              ><el-option
                v-for="name in platform.protocols"
                :key="name"
                :value="name" /></el-select></el-form-item
          ><el-form-item label="因素"
            ><el-select v-model="classic.factors" multiple
              ><el-option
                v-for="value in ['dominance', 'scale', 'density']"
                :key="value"
                :value="value" /></el-select></el-form-item
          ><el-form-item label="标签数"
            ><el-input-number v-model="classic.num_tags" :min="20" :max="200000" /></el-form-item
          ><el-form-item label="重复次数"
            ><el-input-number v-model="classic.total_runs" :min="1" :max="200" /></el-form-item
          ><el-form-item label="每因素采样点"
            ><el-input-number v-model="classic.point_count" :min="2" :max="100" /></el-form-item
          ><el-form-item label="起始 Seed"
            ><el-input-number v-model="classic.seed" :min="0" /></el-form-item
          ><el-form-item label="属性下限"
            ><el-input-number v-model="classic.price_min" :min="1" /></el-form-item
          ><el-form-item label="属性上限"
            ><el-input-number v-model="classic.price_max" :min="2" /></el-form-item
          ><el-form-item label="密度因素最大值"
            ><el-input-number v-model="classic.density_max" :min="10" /></el-form-item
          ><el-form-item label="隔离模式"
            ><el-select v-model="classic.security_mode"
              ><el-option label="严格" value="strict" /><el-option
                label="历史兼容"
                value="legacy" /></el-select></el-form-item
          ><el-form-item label="结果 CSV"><el-input v-model="classic.output" /></el-form-item
          ><el-form-item label="图表输出目录"
            ><el-input v-model="classic.output_dir" /></el-form-item
          ><el-form-item label="演化图类型"
            ><el-select v-model="classic.charts"
              ><el-option
                v-for="value in ['accuracy', 'performance', 'time', 'correction', 'all']"
                :key="value"
                :value="value" /></el-select></el-form-item
        ></el-form>
        <div class="actions">
          <el-button type="primary" :loading="busy" @click="runClassic('robust')"
            >运行敏感度实验</el-button
          ><el-button :loading="busy" @click="runClassic('plot_robust')">绘制敏感度图</el-button
          ><el-button :loading="busy" @click="runClassic('plot_evolution')">绘制进化图</el-button
          ><el-button :loading="busy" @click="runClassic('plot_final')">绘制协议比较图</el-button>
        </div>
        <JsonEvidence v-if="classicResult" :value="classicResult" title="命令、状态与完整输出" />
      </section>
    </el-tab-pane>
  </el-tabs>
  <JobPanel v-if="id" :id="id" />
  <section v-if="preview" class="panel"><AssetPreview :path="preview" /></section>
</template>
