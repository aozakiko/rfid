<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { post, errorText } from '../api'
import type { RegisteredCandidate } from '../types'
const props = defineProps<{
  candidateId?: string
  baselineId?: string
  path?: string
  scene?: string
  profile?: string
  protocol?: string
  family?: 'python' | 'statistics'
}>()
const router = useRouter(),
  busy = ref(false)
async function go(target: 'validation' | 'research') {
  busy.value = true
  try {
    let id = props.candidateId
    if (!id) {
      const result = await post<{ candidate: RegisteredCandidate }>('/api/research/register', {
        family: props.family || 'python',
        path: props.path,
        scene: props.scene,
        task_profile: props.profile,
        protocol: props.protocol,
      })
      id = result.candidate.id
    }
    await router.push({ name: target, query: { candidate: id, baseline: props.baselineId } })
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="actions">
    <el-button v-if="family !== 'statistics'" :loading="busy" @click="go('validation')"
      >验证此候选</el-button
    >
    <el-button type="primary" :loading="busy" @click="go('research')">加入对照实验</el-button>
    <span class="muted">携带冻结源码版本与任务配置</span>
  </div>
</template>
