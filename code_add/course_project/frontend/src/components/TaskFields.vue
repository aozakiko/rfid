<script setup lang="ts">
import { computed } from 'vue'
import { usePlatform } from '../stores/platform'
const model = defineModel<string>({ required: true })
const custom = defineModel<string>('custom', { default: '' })
const platform = usePlatform()
const profile = computed(() => platform.data?.task_profiles[model.value])
</script>
<template>
  <el-form-item label="任务画像" class="full"
    ><el-select v-model="model" aria-label="任务画像"
      ><el-option
        v-for="task in platform.tasks"
        :key="task.value"
        :label="task.title"
        :value="task.value" /></el-select
  ></el-form-item>
  <el-form-item v-if="model === 'custom_task'" label="自定义任务说明" class="full"
    ><el-input
      v-model="custom"
      type="textarea"
      :rows="3"
      placeholder="描述输入属性、输出格式、目标和精度要求"
  /></el-form-item>
  <div v-if="profile" class="muted full space-bottom">{{ profile.output }}</div>
</template>
