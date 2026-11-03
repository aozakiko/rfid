<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { pages } from './router'
import { usePlatform } from './stores/platform'
import { useJobs } from './stores/jobs'
import { errorText } from './api'

const route = useRoute()
const platform = usePlatform()
const jobs = useJobs()
const mobileNav = ref(false)
async function refresh() {
  try {
    await Promise.all([platform.refresh(), jobs.refresh()])
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}
onMounted(refresh)
onBeforeUnmount(() => jobs.dispose())
</script>

<template>
  <div class="workspace">
    <aside class="sidebar" :class="{ open: mobileNav }">
      <RouterLink class="brand" to="/" @click="mobileNav = false">
        <div class="brand-icon">R<span>↗</span></div>
        <div><strong>RFID LAB</strong><small>协议自优化研究平台</small></div>
      </RouterLink>
      <div class="nav-caption">研究工作区</div>
      <nav aria-label="平台功能导航">
        <RouterLink
          v-for="page in pages"
          :key="page.name"
          :to="page.path"
          @click="mobileNav = false"
        >
          <span class="nav-number">{{ page.mark }}</span
          ><span>{{ page.title }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <span class="connection"
          ><i :class="{ offline: platform.error }"></i
          >{{ platform.error ? '连接异常' : '本地研究环境' }}</span
        >
        <p>任务驱动 · 匿名观测 · 可复现实验</p>
        <a href="/legacy" target="_blank" rel="noopener">打开旧版工作台 ↗</a>
      </div>
    </aside>
    <button
      v-if="mobileNav"
      class="nav-mask"
      aria-label="关闭导航"
      @click="mobileNav = false"
    ></button>
    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-left">
          <button class="mobile-toggle" aria-label="展开导航" @click="mobileNav = !mobileNav">
            ☰</button
          ><span class="eyebrow">RESEARCH WORKSPACE</span>
        </div>
        <div class="topbar-actions">
          <span class="running-count" data-testid="active-jobs"
            >{{ jobs.active.length }} 个进行中任务</span
          >
          <el-select
            v-model="platform.scene"
            aria-label="当前场景"
            class="scene-select"
            placeholder="选择场景"
            filterable
          >
            <el-option
              v-for="scene in platform.data?.scenes"
              :key="scene.name"
              :label="scene.name"
              :value="scene.name"
            />
          </el-select>
          <el-button :loading="platform.loading" @click="refresh">刷新</el-button>
        </div>
      </header>
      <main>
        <div class="page-heading">
          <div>
            <div class="eyebrow">RFID PROTOCOL RESEARCH</div>
            <h1>{{ route.meta.title }}</h1>
            <p>{{ route.meta.subtitle }}</p>
          </div>
          <div class="heading-tag">LLM × RFID</div>
        </div>
        <el-alert
          v-if="platform.error"
          type="error"
          :title="platform.error"
          show-icon
          :closable="false"
          class="space-bottom"
          ><template #default
            ><el-button link @click="refresh">重新连接</el-button></template
          ></el-alert
        >
        <RouterView v-if="platform.data" v-slot="{ Component }"
          ><KeepAlive><component :is="Component" /></KeepAlive
        ></RouterView>
        <div v-else-if="platform.loading" class="panel"><el-skeleton :rows="8" animated /></div>
        <el-empty v-else description="尚未取得平台数据"
          ><el-button @click="refresh">重新读取</el-button></el-empty
        >
        <footer>
          基于大模型的 RFID 射频识别通讯协议自优化研究平台<span
            >平台数据与研究证据均来自本地服务</span
          >
        </footer>
      </main>
    </div>
  </div>
</template>
