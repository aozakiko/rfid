<script setup lang="ts">
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { post, errorText } from '../api'
import type { JsonObject } from '../types'
const platform = usePlatform(),
  busy = ref(false),
  result = ref(''),
  models = ref<string[]>([])
const { model } = storeToRefs(platform)
async function probe(mode: 'probe' | 'list') {
  busy.value = true
  try {
    const response = await post<JsonObject>('/api/selfopt/ai-models', { ...platform.model, mode })
    models.value = Array.isArray(response.models) ? (response.models as string[]) : models.value
    result.value = mode === 'list' ? '已读取模型列表' : '连接检查通过'
    ElMessage.success(result.value)
  } catch (error) {
    result.value = errorText(error)
    ElMessage.error(result.value)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>模型连接</h2>
        <p>配置在当前浏览器页面内共享，刷新后需重新填写密钥。</p>
      </div>
      <el-tag :type="platform.modelConfigured ? 'success' : 'info'">{{
        platform.modelConfigured ? '已配置' : '待配置'
      }}</el-tag>
    </div>
    <el-form label-position="top" class="form-grid" @submit.prevent>
      <el-form-item label="API URL" class="full"
        ><el-input
          v-model="model.base_url"
          placeholder="https://your-gateway.example/v1"
          autocomplete="off"
      /></el-form-item>
      <el-form-item label="API Key"
        ><el-input v-model="model.api_key_value" type="password" show-password autocomplete="off"
      /></el-form-item>
      <el-form-item label="模型名称"
        ><el-select
          v-model="model.model"
          filterable
          allow-create
          default-first-option
          placeholder="填写或选择模型"
          ><el-option v-for="name in models" :key="name" :value="name" /></el-select
      ></el-form-item>
      <el-form-item label="Temperature"
        ><el-input-number v-model="model.temperature" :min="0" :max="2" :step="0.1"
      /></el-form-item>
      <el-form-item label="输出 Token 上限"
        ><el-input-number v-model="model.max_tokens" :min="256" :max="16000" :step="256"
      /></el-form-item>
      <el-form-item label="单次请求超时（秒）"
        ><el-input-number v-model="model.timeout_seconds" :min="20" :max="1800"
      /></el-form-item>
      <el-form-item label="网络重试次数"
        ><el-input-number v-model="model.retry_attempts" :min="1" :max="6"
      /></el-form-item>
    </el-form>
    <div class="actions">
      <el-button :loading="busy" @click="probe('probe')">检查连接</el-button
      ><el-button :loading="busy" @click="probe('list')">读取模型列表</el-button
      ><span class="muted">{{ result }}</span>
    </div>
  </section>
</template>
