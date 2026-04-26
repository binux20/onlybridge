<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, nextTick } from 'vue'

const proxies = [
  { id: 'claude', label: 'CLAUDE / 7777' },
  { id: 'opencode', label: 'OPENCODE / 7778' },
  { id: 'openai_compat', label: 'UNIVERSAL / 7779' },
]

const proxy = ref<string>('claude')
const lines = ref<string[]>([])
const frozen = ref(false)
const err = ref('')
const boxEl = ref<HTMLDivElement | null>(null)
let es: EventSource | null = null

function connect() {
  if (es) { es.close(); es = null }
  lines.value = []
  err.value = ''
  es = new EventSource(`/api/logs/${proxy.value}`)
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (typeof data.line === 'string') {
        lines.value.push(data.line)
        if (lines.value.length > 2000) lines.value.splice(0, lines.value.length - 2000)
        if (!frozen.value) nextTick(scrollToEnd)
      }
    } catch {}
  }
  es.onerror = () => { err.value = 'connection lost' }
}

function scrollToEnd() {
  const el = boxEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function lineColor(line: string): string {
  if (/\b(429|402)\b|\[ERROR\]/.test(line)) return 'var(--danger)'
  if (/\[WARN\]/.test(line)) return 'var(--warn)'
  if (/\[MUST\]/.test(line)) return 'var(--accent)'
  if (/\[INFO\]/.test(line)) return 'var(--text-dim)'
  return 'var(--text)'
}

watch(proxy, connect)
onMounted(connect)
onUnmounted(() => { if (es) { es.close(); es = null } })
</script>

<template>
  <h1 class="h-display mb-8">LOGS</h1>

  <div class="flex items-center justify-between mb-6">
    <div class="flex items-center gap-3">
      <div class="label">PROXY</div>
      <select class="input" :style="{ width: '240px' }" v-model="proxy">
        <option v-for="p in proxies" :key="p.id" :value="p.id">{{ p.label }}</option>
      </select>
    </div>
    <button class="btn" :class="frozen ? 'btn-outline' : 'btn-ghost'" @click="frozen = !frozen">
      {{ frozen ? 'RESUME' : 'FREEZE' }}
    </button>
  </div>

  <div
    ref="boxEl"
    class="mono text-[13px] p-4 overflow-auto"
    :style="{ background: 'var(--bg-elev-1)', border: '1px solid var(--border)', height: '60vh', lineHeight: 1.45 }"
  >
    <div v-if="!lines.length" :style="{ color: 'var(--text-muted)' }">WAITING FOR LOGS...</div>
    <div v-for="(l, i) in lines" :key="i" :style="{ color: lineColor(l), whiteSpace: 'pre-wrap' }">{{ l }}</div>
  </div>

  <p v-if="err" class="mt-4 mono text-[12px]" :style="{ color: 'var(--danger)' }">{{ err }}</p>
</template>
