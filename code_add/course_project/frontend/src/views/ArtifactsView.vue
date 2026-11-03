<script setup lang="ts">
import { computed, ref } from 'vue'
import { usePlatform } from '../stores/platform'
import { query, format } from '../api'
import type { Asset } from '../types'
import AssetPreview from '../components/AssetPreview.vue'
const platform = usePlatform(),
  search = ref(''),
  category = ref('all'),
  page = ref(1),
  path = ref('')
const rows = computed(() => {
  const source =
    category.value === 'experiments'
      ? platform.data?.experiment_artifacts || []
      : category.value === 'outputs'
        ? platform.data?.output_artifacts || []
        : [
            ...(platform.data?.experiment_artifacts || []),
            ...(platform.data?.output_artifacts || []),
          ]
  return source.filter(
    (row) =>
      !search.value || (row.name + row.path).toLowerCase().includes(search.value.toLowerCase()),
  )
})
</script>
<template>
  <div class="split wide-right">
    <section class="panel">
      <div class="panel-heading">
        <h2>产物索引</h2>
        <span class="muted">{{ rows.length }} 项</span>
      </div>
      <el-input
        v-model="search"
        clearable
        placeholder="搜索文件名、运行目录或后缀"
        @input="page = 1"
      />
      <div class="actions">
        <el-radio-group v-model="category" @change="page = 1"
          ><el-radio-button value="all">全部</el-radio-button
          ><el-radio-button value="experiments">实验</el-radio-button
          ><el-radio-button value="outputs">Outputs</el-radio-button></el-radio-group
        >
      </div>
      <el-table
        :data="rows.slice((page - 1) * 12, page * 12)"
        @row-click="(row: Asset) => (path = row.path)"
        ><el-table-column label="文件" min-width="180"
          ><template #default="{ row }"
            ><el-button link type="primary">{{ row.name }}</el-button>
            <div class="path">{{ row.path }}</div></template
          ></el-table-column
        ><el-table-column label="大小" width="85"
          ><template #default="{ row }"
            >{{ format((row.size || 0) / 1024, 1) }} KB</template
          ></el-table-column
        ><el-table-column width="65"
          ><template #default="{ row }"
            ><a :href="query('/api/selfopt/download', { path: row.path })" download @click.stop
              >下载</a
            ></template
          ></el-table-column
        ></el-table
      >
      <el-pagination
        v-model:current-page="page"
        :total="rows.length"
        :page-size="12"
        layout="prev, pager, next"
      />
    </section>
    <section class="panel"><AssetPreview :path="path" /></section>
  </div>
</template>
