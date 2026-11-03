<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])
const props = defineProps<{
  labels: string[]
  series: { name: string; data: (number | null)[] }[]
  unit: string
  label: string
}>()
const host = ref<HTMLDivElement>()
let chart: EChartsType | undefined, observer: ResizeObserver | undefined
function draw() {
  chart?.setOption(
    {
      animation: false,
      grid: { left: 65, right: 24, top: 45, bottom: 70 },
      tooltip: { trigger: 'axis', renderMode: 'richText' },
      legend: { top: 0, type: 'scroll' },
      xAxis: {
        type: 'category',
        data: props.labels,
        axisLabel: { rotate: props.labels.length > 4 ? 20 : 0, width: 110, overflow: 'truncate' },
      },
      yAxis: { type: 'value', name: props.unit, splitLine: { lineStyle: { color: '#edf2f3' } } },
      series: props.series.map((row) => ({
        ...row,
        type: 'line',
        connectNulls: false,
        symbolSize: 7,
      })),
    },
    true,
  )
}
watch(() => [props.labels, props.series, props.unit], draw, { deep: true })
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
<template><div ref="host" class="chart" role="img" :aria-label="label"></div></template>
