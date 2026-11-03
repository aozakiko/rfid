<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import { post, errorText, terminal } from '../api'
import type { JsonObject } from '../types'
import ModelSettings from '../components/ModelSettings.vue'
import TaskFields from '../components/TaskFields.vue'
import JobPanel from '../components/JobPanel.vue'
import JsonEvidence from '../components/JsonEvidence.vue'
import AssetPreview from '../components/AssetPreview.vue'
import CandidateActions from '../components/CandidateActions.vue'
const platform = usePlatform(),
  jobs = useJobs()
const form = reactive({
  parent_a: '',
  parent_b: '',
  task_profile: 'mode_query',
  custom_task_text: '',
  protocol_name: 'web_vue_candidate',
  target_generation: 'next',
  max_repair_rounds: 1,
  validate_num_tags: 80,
  requirement_text: '在精度要求下控制通信成本，处理完整任务语义。',
})
const id = ref(''),
  busy = ref(false),
  path = ref(''),
  promptResult = ref<JsonObject | null>(null)
const job = computed(() => jobs.records[id.value])
const result = computed(() => job.value?.result?.result as JsonObject | undefined)
watch(
  () => job.value?.status,
  async (status) => {
    if (status && terminal(status)) {
      const value = result.value
      path.value = String(
        value?.status === 'dry_run'
          ? value.prompt_file || ''
          : value?.target_file || value?.prompt_file || '',
      )
      await platform.refresh().catch(() => {})
    }
  },
)
watch(
  () => platform.scene,
  () => {
    form.parent_a = platform.protocols[0] || ''
    form.parent_b = platform.protocols[1] || ''
  },
  { immediate: true },
)
watch(
  () => form.task_profile,
  (value) => {
    form.protocol_name = 'web_' + value + '_candidate'
  },
)
async function run(preview: boolean) {
  if (!form.parent_a || !form.parent_b || form.parent_a === form.parent_b) {
    ElMessage.error('请选择两个不同的父代')
    return
  }
  if (form.task_profile === 'custom_task' && !form.custom_task_text.trim()) {
    ElMessage.error('请填写自定义任务说明')
    return
  }
  if (!preview && !platform.modelConfigured) {
    ElMessage.error('请先填写模型 URL、Key 和名称')
    return
  }
  busy.value = true
  try {
    const job = await jobs.submit('/api/selfopt/ai-run-async', {
      ...platform.model,
      ...form,
      scene: platform.scene,
      dry_run: preview,
      run_mode: preview ? 'preview' : 'generate',
    })
    id.value = job.id
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function prompts() {
  try {
    promptResult.value = await post('/api/selfopt/prompts', {
      scene: platform.scene,
      parents: [form.parent_a, form.parent_b],
    })
    await platform.refresh()
    ElMessage.success('交叉 Prompt 已生成')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
</script>
<template>
  <div class="split">
    <ModelSettings />
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>单次候选生成</h2>
          <p>任务画像同时决定生成约束和验证语义。</p>
        </div>
      </div>
      <el-form label-position="top" class="form-grid">
        <TaskFields v-model="form.task_profile" v-model:custom="form.custom_task_text" />
        <el-form-item label="父代 A"
          ><el-select v-model="form.parent_a" filterable
            ><el-option v-for="name in platform.protocols" :key="name" :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="父代 B"
          ><el-select v-model="form.parent_b" filterable
            ><el-option v-for="name in platform.protocols" :key="name" :value="name" /></el-select
        ></el-form-item>
        <el-form-item label="新协议名称"><el-input v-model="form.protocol_name" /></el-form-item
        ><el-form-item label="目标世代"><el-input v-model="form.target_generation" /></el-form-item>
        <el-form-item label="最大修复轮数"
          ><el-input-number v-model="form.max_repair_rounds" :min="0" :max="8" /></el-form-item
        ><el-form-item label="验证标签数"
          ><el-input-number v-model="form.validate_num_tags" :min="20" :max="5000"
        /></el-form-item>
        <el-form-item label="需求与约束" class="full"
          ><el-input v-model="form.requirement_text" type="textarea" :rows="3"
        /></el-form-item>
      </el-form>
      <div class="actions">
        <el-button :loading="busy" :disabled="job && !terminal(job.status)" @click="run(true)"
          >预览生成任务</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="job && !terminal(job.status)"
          @click="run(false)"
          >生成协议代码</el-button
        ><el-button link @click="prompts">生成交叉 Prompt</el-button>
      </div>
    </section>
  </div>
  <JobPanel v-if="id" :id="id" />
  <section v-if="job?.result || promptResult" class="panel">
    <div class="panel-heading"><h2>生成产物</h2></div>
    <el-alert
      v-if="result?.status === 'dry_run'"
      title="预览仅生成 Prompt，没有调用模型或创建协议源码。"
      type="info"
      :closable="false"
      class="space-bottom"
    />
    <template v-if="result && result.status !== 'dry_run'">
      <p>
        {{
          result.status === 'success'
            ? '候选已生成并完成后端验证，可继续进行跨场景对照实验。'
            : '候选未达到生成工作流要求，请检查验证阶段和修复记录。'
        }}
      </p>
      <CandidateActions v-if="result.candidate_id" :candidate-id="String(result.candidate_id)" />
    </template>
    <AssetPreview v-if="path" :path="path" /><JsonEvidence :value="job?.result || promptResult" />
  </section>
</template>
