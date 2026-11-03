<script setup lang="ts">
import { computed, ref, onBeforeUnmount } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePlatform } from '../stores/platform'
import { request, post, query, errorText } from '../api'
import type { Asset } from '../types'
import AssetPreview from '../components/AssetPreview.vue'
const platform = usePlatform()
const search = ref(''),
  page = ref(1),
  busy = ref(false),
  creating = ref(false),
  sceneName = ref(''),
  baseline = ref(''),
  generation = ref('generation_0')
const file = ref<{ path: string; revision: string; content: string } | null>(null),
  buffer = ref(''),
  saved = ref(''),
  preview = ref('')
const dirty = computed(() => file.value !== null && buffer.value !== saved.value)
const files = computed<Asset[]>(() => {
  const scene = platform.currentScene
  if (!scene) return []
  return [
    { name: 'Task.py', path: scene.task_path, generation: '配置' },
    ...(scene.config_path
      ? [{ name: 'config.py', path: scene.config_path, generation: '配置' }]
      : []),
    ...scene.protocol_files,
    ...scene.prompts,
    ...(platform.data?.base_protocols || []).map((row) => ({
      ...row,
      generation: 'base_protocol',
    })),
  ].filter(
    (row) =>
      !search.value || (row.name + row.path).toLowerCase().includes(search.value.toLowerCase()),
  )
})
async function mayDiscard() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前文件有未保存的修改，是否放弃这些修改？', '未保存的文件', {
      confirmButtonText: '放弃修改',
      cancelButtonText: '继续编辑',
      type: 'warning',
    })
    buffer.value = saved.value
    return true
  } catch {
    return false
  }
}
async function open(path: string) {
  if (busy.value) return
  if (!(await mayDiscard())) return
  if (busy.value) return
  busy.value = true
  try {
    file.value = await request(query('/api/selfopt/file', { path }))
    buffer.value = file.value!.content
    saved.value = buffer.value
  } catch (error) {
    ElMessage.error(errorText(error))
    preview.value = path
  } finally {
    busy.value = false
  }
}
async function save() {
  if (!file.value || busy.value) return
  const content = buffer.value,
    current = file.value.path
  busy.value = true
  try {
    const result = await post<{ revision: string }>('/api/selfopt/file', {
      path: current,
      revision: file.value.revision,
      content,
    })
    if (file.value.path === current) {
      file.value.revision = result.revision
      saved.value = content
    }
    ElMessage.success('文件已保存')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function create() {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(sceneName.value)) {
    ElMessage.error('场景名请使用字母、数字和下划线，以字母或下划线开头')
    return
  }
  busy.value = true
  try {
    await post('/api/selfopt/scene', { scene: sceneName.value })
    await platform.refresh()
    platform.scene = sceneName.value
    creating.value = false
    ElMessage.success('场景已创建')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
async function copy() {
  if (!baseline.value) return
  busy.value = true
  try {
    await post('/api/selfopt/baseline-copy', {
      scene: platform.scene,
      protocol: baseline.value,
      target_generation: generation.value,
    })
    await platform.refresh()
    ElMessage.success('基线已复制')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}
const uploadDestination = ref('output'),
  uploadName = ref('')
async function upload(event: Event) {
  const target = event.target as HTMLInputElement,
    selected = target.files?.[0]
  if (!selected) return
  if (selected.size > 10 * 1024 * 1024) {
    ElMessage.error('文件最多 10 MB')
    target.value = ''
    return
  }
  busy.value = true
  try {
    const result = await request<{ path: string }>(
      query('/api/selfopt/upload', {
        name: uploadName.value || selected.name,
        destination: uploadDestination.value,
        scene: platform.scene,
        generation: generation.value,
      }),
      { method: 'POST', body: selected, headers: { 'Content-Type': 'application/octet-stream' } },
    )
    await platform.refresh()
    preview.value = result.path
    ElMessage.success('文件已导入')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    target.value = ''
    busy.value = false
  }
}
onBeforeRouteLeave(mayDiscard)
const beforeUnload = (event: BeforeUnloadEvent) => {
  if (dirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
const saveShortcut = (event: KeyboardEvent) => {
  if (
    (event.ctrlKey || event.metaKey) &&
    event.key.toLowerCase() === 's' &&
    event.target instanceof Element &&
    event.target.closest('.text-editor')
  ) {
    event.preventDefault()
    if (dirty.value && !busy.value) void save()
  }
}
window.addEventListener('beforeunload', beforeUnload)
window.addEventListener('keydown', saveShortcut)
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  window.removeEventListener('keydown', saveShortcut)
})
</script>
<template>
  <div class="split wide-right">
    <div>
      <section class="panel">
        <div class="panel-heading">
          <h2>场景与基线</h2>
          <el-button @click="creating = true">新建场景</el-button>
        </div>
        <el-form label-position="top"
          ><el-form-item label="复制基线"
            ><el-select v-model="baseline" placeholder="选择基线协议" filterable
              ><el-option
                v-for="item in platform.data?.base_protocols"
                :key="item.name"
                :label="item.name"
                :value="item.name" /></el-select></el-form-item
          ><el-form-item label="目标世代"><el-input v-model="generation" /></el-form-item
        ></el-form>
        <el-button :disabled="!baseline" :loading="busy" @click="copy"
          >复制到 {{ platform.scene }}</el-button
        >
      </section>
      <section class="panel">
        <div class="panel-heading">
          <h2>文件资产</h2>
          <span class="muted">{{ files.length }} 个文件</span>
        </div>
        <el-input v-model="search" placeholder="搜索文件名或路径" clearable @input="page = 1" />
        <el-table
          :data="files.slice((page - 1) * 10, page * 10)"
          @row-click="(row: Asset) => open(row.path)"
          ><el-table-column label="文件" min-width="180"
            ><template #default="{ row }"
              ><el-button link type="primary">{{ row.name }}</el-button>
              <div class="path">{{ row.generation || row.path }}</div></template
            ></el-table-column
          ></el-table
        >
        <el-pagination
          v-model:current-page="page"
          :total="files.length"
          :page-size="10"
          layout="prev, pager, next"
        />
      </section>
      <section class="panel">
        <div class="panel-heading"><h2>导入文件</h2></div>
        <el-form label-position="top"
          ><el-form-item label="导入位置"
            ><el-select v-model="uploadDestination"
              ><el-option label="运行产物" value="output" /><el-option
                label="当前场景协议"
                value="protocol" /><el-option label="当前场景 Prompt" value="prompt" /><el-option
                label="当前场景 Outputs"
                value="scene_output" /><el-option
                label="Experiments 产物"
                value="experiment" /></el-select></el-form-item
          ><el-form-item label="保存名称（留空沿用原名）"
            ><el-input v-model="uploadName" /></el-form-item></el-form
        ><input aria-label="选择导入文件" type="file" :disabled="busy" @change="upload" />
      </section>
    </div>
    <section class="panel" v-loading="busy">
      <div class="panel-heading">
        <div>
          <h2>源码编辑器</h2>
          <p>{{ dirty ? '有未保存的修改' : file ? '已与磁盘同步' : '从左侧选择文件' }}</p>
        </div>
        <div>
          <el-button :disabled="!file" @click="open(file!.path)">重新读取</el-button
          ><el-button type="primary" :disabled="!file || !dirty" @click="save">保存文件</el-button>
        </div>
      </div>
      <div class="path space-bottom">{{ file?.path }}</div>
      <textarea
        v-model="buffer"
        class="text-editor"
        aria-label="源码编辑器"
        spellcheck="false"
        :disabled="!file"
      />
      <p class="muted">保存时校验文件版本。磁盘内容已变化时会拒绝覆盖，保留当前编辑内容。</p>
      <el-collapse
        ><el-collapse-item title="文件预览"
          ><AssetPreview :path="preview || file?.path || ''" /></el-collapse-item
      ></el-collapse>
    </section>
  </div>
  <el-dialog v-model="creating" title="创建新场景" width="min(480px, 94vw)"
    ><el-form label-position="top"
      ><el-form-item label="场景名称"
        ><el-input v-model="sceneName" placeholder="例如 warehouse_mean" /></el-form-item
    ></el-form>
    <p class="muted">创建 Task.py、配置、初始世代和基线协议副本。</p>
    <template #footer
      ><el-button @click="creating = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="create">创建</el-button></template
    ></el-dialog
  >
</template>
