<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { percent, format } from '../api'
import type { SelectionReport } from '../types'
use([ScatterChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])
const props = defineProps<{ report: SelectionReport }>()
const host = ref<HTMLDivElement>()
let chart: EChartsType | undefined, observer: ResizeObserver | undefined
const reasons: Record<string, string> = {
  duplicate_structure: '源码结构重复',
  pareto_front: '首层 Pareto 前沿',
  dominated_front: '被其他前沿支配',
  crowding_capacity: '按拥挤度控制种群容量',
  exploration_below_precision: '精度尚未达标，保留探索',
  exploration_unmeasured: '成本未独立计量，仅用于探索',
  unranked_source_parent: '未获排名的源码父代',
  validation_failed: '验证未通过',
  population_capacity: '种群容量限制',
  ranked_by_quality_only: '按任务质量排序',
  ranked_by_precision_constrained_cost: '精度达标后按成本排序',
}
const points = computed(() =>
  props.report.decisions.filter((row) => row.feasible && !row.duplicate_of),
)
function draw() {
  chart?.setOption(
    {
      animation: false,
      grid: { left: 70, right: 25, top: 42, bottom: 50 },
      tooltip: { trigger: 'item', renderMode: 'richText' },
      legend: { top: 0 },
      xAxis: { type: 'value', name: '质量分', min: 0, max: 1, nameLocation: 'middle', nameGap: 26 },
      yAxis: { type: 'value', name: '模拟通信 ms' },
      series: [
        {
          name: '首层前沿',
          type: 'scatter',
          symbolSize: 13,
          data: points.value
            .filter((row) => row.pareto_rank === 1)
            .map((row) => ({
              name: row.name,
              value: [row.quality_score, row.mean_communication_cost_ms],
            })),
        },
        {
          name: '其余达标候选',
          type: 'scatter',
          symbolSize: 9,
          data: points.value
            .filter((row) => row.pareto_rank !== 1)
            .map((row) => ({
              name: row.name,
              value: [row.quality_score, row.mean_communication_cost_ms],
            })),
        },
      ],
    },
    true,
  )
}
watch(() => props.report, draw, { deep: true })
watch(
  host,
  (element) => {
    observer?.disconnect()
    chart?.dispose()
    chart = undefined
    if (element) {
      chart = init(element)
      observer = new ResizeObserver(() => chart?.resize())
      observer.observe(element)
      draw()
    }
  },
  { flush: 'post' },
)
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <div class="space-bottom">
    <p>
      达标唯一候选 {{ report.feasible_count }} 个；本次比较排除结构重复
      {{ report.duplicates_removed }} 个。
      {{
        report.objective === 'quality_only'
          ? '代表候选按语义通过率和质量确定。'
          : '有达标前沿时，代表赢家在独立验证前按前沿最低成本确定；尚未达标的候选只用于探索。'
      }}
    </p>
    <div
      v-if="report.objective === 'pareto_diversity'"
      ref="host"
      class="chart"
      role="img"
      aria-label="质量与通信成本 Pareto 前沿"
    ></div>
    <el-table :data="report.decisions" max-height="360">
      <el-table-column prop="name" label="候选" min-width="170" />
      <el-table-column label="保留" width="75"
        ><template #default="{ row }">{{ row.retained ? '是' : '否' }}</template></el-table-column
      >
      <el-table-column label="通过率 / 质量" min-width="145"
        ><template #default="{ row }"
          >{{ percent(row.quality_pass_rate) }} / {{ format(row.quality_score, 4) }}</template
        ></el-table-column
      >
      <el-table-column label="模拟成本" min-width="110"
        ><template #default="{ row }"
          >{{ format(row.mean_communication_cost_ms) }} ms</template
        ></el-table-column
      >
      <el-table-column label="前沿 / 拥挤度" min-width="135"
        ><template #default="{ row }"
          >{{ row.pareto_rank ?? '—' }} /
          {{ row.boundary ? '边界' : format(row.crowding_distance, 3) }}</template
        ></el-table-column
      >
      <el-table-column label="选择原因" min-width="220"
        ><template #default="{ row }"
          >{{ reasons[row.reason] || row.reason }}
          <div v-if="row.duplicate_of" class="muted">与 {{ row.duplicate_of }} 重复</div></template
        ></el-table-column
      >
    </el-table>
  </div>
</template>
