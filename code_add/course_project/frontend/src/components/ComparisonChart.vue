<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { ScatterChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComparisonRow } from '../types'
use([ScatterChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])
const props = defineProps<{ rows: ComparisonRow[]; axis: string; singleScenario: boolean }>()
const host = ref<HTMLDivElement>()
let chart: EChartsType | undefined, observer: ResizeObserver | undefined
function draw() {
  const valid = props.rows.filter(
    (row) =>
      row.mean_error !== null &&
      row[props.axis] !== null &&
      Number.isFinite(Number(row.mean_error)) &&
      Number.isFinite(Number(row[props.axis])),
  )
  const points = valid.map((row) => ({
    name: row.protocol,
    value: [Number(row[props.axis]), row.mean_error],
    symbolSize: row.is_pareto ? 15 : 10,
    itemStyle: { color: row.is_pareto ? '#187e73' : row.pareto_eligible ? '#5a7f9a' : '#b7c3c8' },
  }))
  const frontier = props.singleScenario
    ? valid
        .filter((row) => row.is_pareto)
        .sort((a, b) => Number(a[props.axis]) - Number(b[props.axis]))
        .map((row) => [Number(row[props.axis]), row.mean_error])
    : []
  chart?.setOption(
    {
      animation: false,
      grid: { left: 70, right: 35, top: 35, bottom: 65 },
      tooltip: { trigger: 'item', renderMode: 'richText' },
      xAxis: {
        name:
          props.axis === 'mean_communication_bits'
            ? '平均通信 bits'
            : props.axis === 'mean_slots'
              ? '平均时隙数'
              : '平均轮数',
        nameLocation: 'middle',
        nameGap: 40,
        type: 'value',
        splitLine: { lineStyle: { color: '#eef2f3' } },
      },
      yAxis: {
        name: '误差（越低越好）',
        type: 'value',
        splitLine: { lineStyle: { color: '#eef2f3' } },
      },
      series: [
        { type: 'scatter', data: points },
        {
          type: 'line',
          data: frontier,
          lineStyle: { color: '#187e73', type: 'dashed' },
          symbol: 'none',
          silent: true,
        },
      ],
    },
    true,
  )
}
watch(() => [props.rows, props.axis, props.singleScenario], draw, { deep: true })
onMounted(() => {
  if (host.value) {
    chart = init(host.value)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(host.value)
    draw()
  }
})
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <div ref="host" class="chart" role="img" aria-label="协议通信成本与误差比较图"></div>
</template>
