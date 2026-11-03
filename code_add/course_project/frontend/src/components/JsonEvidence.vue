<script setup lang="ts">
defineProps<{ value: unknown; title?: string }>()
</script>
<template>
  <el-collapse
    ><el-collapse-item :title="title || '完整记录与证据'">
      <pre class="json">{{ JSON.stringify(value, null, 2) }}</pre>
    </el-collapse-item></el-collapse
  >
</template>
