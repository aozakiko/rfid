<script setup lang="ts">
import { computed } from 'vue'
import { usePlatform } from '../stores/platform'
import { useJobs } from '../stores/jobs'
import type { Scene } from '../types'
import StatusTag from '../components/StatusTag.vue'
const platform = usePlatform(),
  jobs = useJobs()
const metrics = computed(() => [
  ['实验场景', platform.data?.scenes.length || 0, '场景与生成器'],
  [
    '协议资产',
    platform.data?.scenes.reduce((n, scene) => n + scene.protocol_count, 0) || 0,
    '各代候选与源码',
  ],
  ['验证记录', platform.data?.database_counts.validation_reports || 0, '执行、隔离与语义检查'],
  ['后台任务', jobs.active.length, '正在执行或排队'],
])
</script>
<template>
  <section class="hero">
    <div>
      <div class="eyebrow">TASK → GENERATE → VALIDATE → EVOLVE</div>
      <h2>让协议设计成为可验证的研究过程</h2>
      <p>{{ platform.data?.project_vision.name }}</p>
      <div class="flow">
        <template
          v-for="(step, index) in ['任务需求', '协议生成', '沙箱验证', '迭代选择', '独立实验']"
          :key="step"
          ><i v-if="index">→</i><span>{{ step }}</span></template
        >
      </div>
    </div>
    <RouterLink to="/optimization"
      ><el-button type="primary" size="large">进入自动迭代 ↗</el-button></RouterLink
    >
  </section>
  <div class="metric-grid">
    <div v-for="metric in metrics" :key="String(metric[0])" class="metric">
      <span>{{ metric[0] }}</span
      ><strong>{{ metric[1] }}</strong
      ><small>{{ metric[2] }}</small>
    </div>
  </div>
  <div class="split">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>场景资产概览</h2>
          <p>选择顶部场景，贯穿设计、验证与迭代。</p>
        </div>
        <RouterLink to="/assets">管理资产 →</RouterLink>
      </div>
      <el-table
        :data="platform.data?.scenes"
        @row-click="(row: Scene) => (platform.scene = row.name)"
        ><el-table-column prop="name" label="场景" min-width="150" /><el-table-column
          prop="generation_count"
          label="世代"
          width="65" /><el-table-column
          prop="protocol_count"
          label="协议"
          width="65" /><el-table-column prop="prompt_count" label="Prompt" width="80"
      /></el-table>
    </section>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>最近后台任务</h2>
          <p>刷新页面后仍可查看服务保存的任务状态。</p>
        </div>
        <RouterLink to="/audit">运行审计 →</RouterLink>
      </div>
      <el-empty v-if="!jobs.history.length" description="尚无后台任务" :image-size="70" />
      <div v-for="job in jobs.history.slice(0, 6)" :key="job.id" class="result-line">
        <div>
          {{ job.kind }}
          <div class="path">{{ job.created_at }} · {{ job.elapsed_s }} 秒</div>
        </div>
        <StatusTag :status="job.status" />
      </div>
    </section>
  </div>
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>研究工具</h2>
        <p>围绕任务质量、通信成本和复现证据组织工作。</p>
      </div>
    </div>
    <div class="split">
      <div>
        <h3>设计与验证</h3>
        <p class="muted">
          配置任务画像和模型，生成候选后检查 AST、白名单、运行时隔离、物理参数与语义结果。
        </p>
        <RouterLink to="/design">配置模型与协议生成 →</RouterLink>
      </div>
      <div>
        <h3>比较与实验</h3>
        <p class="muted">
          按同一场景比较候选，查看误差、通信统计、种子、置信区间与可追溯的原始结果。
        </p>
        <RouterLink to="/research">冻结候选并运行跨场景评测 →</RouterLink>
        <p><RouterLink to="/statistics">查看统计基准 →</RouterLink></p>
      </div>
    </div>
  </section>
</template>
