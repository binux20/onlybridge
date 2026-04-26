<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Chart, registerables } from 'chart.js'
import { api, type StatsSummary, type TimeseriesPoint } from '../api'
import { t } from '../i18n'

Chart.register(...registerables)

const period = ref<'today' | 'week' | 'all'>('all')
const data = ref<StatsSummary | null>(null)
const series = ref<TimeseriesPoint[]>([])
const err = ref('')
const chartEl = ref<HTMLCanvasElement | null>(null)
const hasTiktoken = ref(true)
const tiktokenCmd = ref('py -m pip install tiktoken')
let chart: Chart | null = null
let sig = ''
let timer: number | undefined

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

async function load() {
  try {
    const [s, t] = await Promise.all([api.stats(period.value), api.timeseries(14)])
    data.value = s
    series.value = t
    drawChart()
  } catch (e: any) { err.value = String(e) }
}

function drawChart() {
  if (!chartEl.value) return
  const next = JSON.stringify(series.value)
  if (next === sig && chart) return
  sig = next
  const labels = series.value.map(p => p.date)
  const reqs = series.value.map(p => p.requests)
  const accent = cssVar('--accent') || '#FF7A1A'
  const border = cssVar('--border') || '#1f1f1f'
  const muted = cssVar('--text-muted') || '#555'

  if (!chart) {
    chart = new Chart(chartEl.value, {
      type: 'line',
      data: { labels, datasets: [{ data: reqs, borderColor: accent, borderWidth: 1.5, pointRadius: 3, pointBackgroundColor: accent, pointStyle: 'rect', tension: 0, fill: false }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { backgroundColor: cssVar('--bg-elev-2') || '#111', borderColor: border, borderWidth: 1, titleFont: { family: 'JetBrains Mono' }, bodyFont: { family: 'JetBrains Mono' } } },
        scales: {
          x: { grid: { color: border }, ticks: { color: muted, font: { family: 'JetBrains Mono', size: 11 } } },
          y: { grid: { color: border }, ticks: { color: muted, font: { family: 'JetBrains Mono', size: 11 } }, beginAtZero: true },
        },
      },
    })
  } else {
    chart.data.labels = labels
    chart.data.datasets[0].data = reqs
    chart.update('none')
  }
}

async function loadTiktoken() {
  try {
    const r = await api.tokensStatus()
    hasTiktoken.value = r.has_tiktoken
    tiktokenCmd.value = r.install_cmd
  } catch {}
}

watch(period, load)
onMounted(() => { load(); loadTiktoken(); timer = window.setInterval(load, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer); chart?.destroy(); chart = null })
</script>

<template>
  <h1 class="h-display mb-2">{{ t('stats.title') }}</h1>
  <p class="mono text-[12px] mb-8" :style="{ color: 'var(--text-muted)' }">
    {{ t('stats.note') }}
    <a href="http://my.onlysq.ru/usage" target="_blank" rel="noopener" :style="{ color: 'var(--accent)' }" class="hover:underline">{{ t('stats.note.link') }}</a>
  </p>

  <div v-if="!hasTiktoken" class="card mb-6" :style="{ padding: '14px 16px', borderColor: 'var(--warn)' }">
    <div class="label mb-2" :style="{ color: 'var(--warn)' }">TIKTOKEN</div>
    <p class="text-[13px] mb-2" :style="{ color: 'var(--text)' }">{{ t('stats.tiktoken.warn') }}</p>
    <code class="mono text-[12px] inline-block px-2 py-1 mb-2" :style="{ background: 'var(--bg-elev-2)', border: '1px solid var(--border)' }">{{ tiktokenCmd }}</code>
    <p class="text-[12px]" :style="{ color: 'var(--text-muted)' }">
      {{ t('stats.note') }}
      <a href="http://my.onlysq.ru/usage" target="_blank" rel="noopener" :style="{ color: 'var(--accent)' }" class="hover:underline">{{ t('stats.note.link') }}</a>
    </p>
  </div>

  <div class="flex items-center gap-2 mb-8">
    <button class="btn" :class="period === 'today' ? 'btn-primary' : 'btn-ghost'" @click="period = 'today'">TODAY</button>
    <button class="btn" :class="period === 'week'  ? 'btn-primary' : 'btn-ghost'" @click="period = 'week'">WEEK</button>
    <button class="btn" :class="period === 'all'   ? 'btn-primary' : 'btn-ghost'" @click="period = 'all'">ALL</button>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-6 mb-12">
    <div class="card">
      <div class="label mb-2">REQUESTS</div>
      <div class="mono text-[28px]">{{ data?.totals.requests ?? 0 }}</div>
    </div>
    <div class="card">
      <div class="label mb-2">TOKENS</div>
      <div class="mono text-[28px]">{{ data?.totals.total_tokens ?? 0 }}</div>
    </div>
    <div class="card">
      <div class="label mb-2">AVG LATENCY</div>
      <div class="mono text-[28px]">{{ data?.totals.avg_latency_ms ?? 0 }}<span class="text-[14px] ml-1" :style="{ color: 'var(--text-muted)' }">MS</span></div>
    </div>
    <div class="card">
      <div class="label mb-2">SUCCESS</div>
      <div class="mono text-[28px]">{{ Math.round(data?.totals.success_rate ?? 100) }}<span class="text-[14px] ml-1" :style="{ color: 'var(--text-muted)' }">%</span></div>
    </div>
  </div>

  <div class="card mb-12">
    <div class="label mb-4">TIMESERIES / 14 DAYS</div>
    <div :style="{ height: '260px' }"><canvas ref="chartEl" /></div>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
    <div class="card">
      <div class="label mb-4">BY SOURCE</div>
      <div v-if="!data?.by_source.length" :style="{ color: 'var(--text-muted)' }" class="mono text-[13px]">NO DATA</div>
      <div v-for="r in data?.by_source" :key="r.source" class="flex justify-between py-2 border-b" :style="{ borderColor: 'var(--border-soft)' }">
        <span class="mono text-[13px]">{{ r.source.toUpperCase() }}</span>
        <span class="mono text-[13px]" :style="{ color: 'var(--text-dim)' }">{{ r.requests }} REQ / {{ r.prompt_tokens + r.completion_tokens }} TOK</span>
      </div>
    </div>
    <div class="card">
      <div class="label mb-4">BY MODEL</div>
      <div v-if="!data?.by_model.length" :style="{ color: 'var(--text-muted)' }" class="mono text-[13px]">NO DATA</div>
      <div v-for="r in data?.by_model" :key="r.model" class="flex justify-between py-2 border-b" :style="{ borderColor: 'var(--border-soft)' }">
        <span class="mono text-[13px]">{{ r.model }}</span>
        <span class="mono text-[13px]" :style="{ color: 'var(--text-dim)' }">{{ r.requests }} REQ</span>
      </div>
    </div>
  </div>

  <p v-if="err" class="mt-6 mono text-[12px]" :style="{ color: 'var(--danger)' }">{{ err }}</p>
</template>
