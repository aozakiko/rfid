<script setup lang="ts">
import { computed } from 'vue'
import { statusLabel } from '../api'
const props = defineProps<{ status: string }>()
const type = computed(() =>
  ['success', 'passed', 'evaluated'].includes(props.status)
    ? 'success'
    : ['failed', 'interrupted'].includes(props.status)
      ? 'danger'
      : ['warning', 'cancelled', 'execution_only'].includes(props.status)
        ? 'warning'
        : 'info',
)
</script>
<template>
  <el-tag :type="type" effect="light" size="small">{{ statusLabel(status) }}</el-tag>
</template>
