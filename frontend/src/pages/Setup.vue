<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed, watch } from 'vue'
import { api, type AppConfig, type ProxyInfo, type SetupResult, type ModelInfo } from '../api'
import { t } from '../i18n'

type ToolId = 'claude' | 'opencode' | 'openai_compat'

const cfg = ref<AppConfig | null>(null)
const keyInput = ref('')
const saving = ref(false)
const err = ref<string>('')

const tools = computed(() => [
  { id: 'claude' as const,        title: t('setup.tool.claude.title'),        desc: t('setup.tool.claude.desc') },
  { id: 'opencode' as const,      title: t('setup.tool.opencode.title'),      desc: t('setup.tool.opencode.desc') },
  { id: 'openai_compat' as const, title: t('setup.tool.openai_compat.title'), desc: t('setup.tool.openai_compat.desc') },
])

const statuses = ref<Record<ToolId, ProxyInfo | null>>({
  claude: null, opencode: null, openai_compat: null,
})
const busy = ref<Record<ToolId, boolean>>({
  claude: false, opencode: false, openai_compat: false,
})
const preview = ref<{ tool: ToolId; data: SetupResult } | null>(null)

const models = ref<ModelInfo[]>([])
const mainModel = ref('')
const subModel = ref('')
const savedMain = ref('')
const savedSub = ref('')
const mainRpm = ref<number>(3)
const subRpm = ref<number>(10)
const savedMainRpm = ref<number>(3)
const savedSubRpm = ref<number>(10)
const targetProxy = ref<ToolId>('claude')
const modelSaving = ref(false)
const modelSaved = ref(false)

let timer: number | undefined

function modelsForProxy(c: AppConfig, tool: ToolId): { main: string; sub: string } {
  const pm = (c as any).proxy_models?.[tool] || {}
  return {
    main: (pm.main || '').toString().trim() || c.main_model,
    sub: (pm.sub || '').toString().trim() || c.sub_model,
  }
}

function rpmForProxy(c: AppConfig, tool: ToolId): { main: number; sub: number } {
  const pr = (c as any).proxy_rpm?.[tool] || {}
  const m = parseInt(pr.main, 10)
  const s = parseInt(pr.sub, 10)
  return {
    main: Number.isFinite(m) && m >= 1 ? m : 3,
    sub: Number.isFinite(s) && s >= 1 ? s : 10,
  }
}

const hasUnsaved = computed(() =>
  mainModel.value !== savedMain.value
  || subModel.value !== savedSub.value
  || mainRpm.value !== savedMainRpm.value
  || subRpm.value !== savedSubRpm.value
)

async function loadAll() {
  try {
    const c = await api.getConfig()
    cfg.value = c
    const cur = modelsForProxy(c, targetProxy.value)
    mainModel.value = cur.main
    subModel.value = cur.sub
    savedMain.value = cur.main
    savedSub.value = cur.sub
    const rpm = rpmForProxy(c, targetProxy.value)
    mainRpm.value = rpm.main
    subRpm.value = rpm.sub
    savedMainRpm.value = rpm.main
    savedSubRpm.value = rpm.sub
  } catch (e: any) { err.value = String(e) }
  await Promise.all((['claude', 'opencode', 'openai_compat'] as ToolId[]).map(async id => {
    try {
      const r = await api.setupStatus(id)
      statuses.value[id] = r.proxy
    } catch {}
  }))
}

async function loadModels() {
  try {
    const r = await api.listModels()
    models.value = r.items
  } catch (e: any) { err.value = String(e) }
}

async function saveKey() {
  if (!keyInput.value.trim()) return
  saving.value = true
  try {
    await api.patchConfig({ onlysq_key: keyInput.value.trim() })
    keyInput.value = ''
    await loadAll()
  } catch (e: any) { err.value = String(e) }
  finally { saving.value = false }
}

async function showPreview(tool: ToolId) {
  busy.value[tool] = true
  try {
    const data = await api.setupPreview(tool)
    preview.value = { tool, data }
  } catch (e: any) { err.value = String(e) }
  finally { busy.value[tool] = false }
}

async function confirmStart(tool: ToolId) {
  busy.value[tool] = true
  preview.value = null
  try {
    const r = await api.setupStart(tool, true)
    statuses.value[tool] = r.proxy
  } catch (e: any) { err.value = String(e) }
  finally { busy.value[tool] = false }
}

async function stopTool(tool: ToolId) {
  busy.value[tool] = true
  try {
    const r = await api.setupStop(tool)
    statuses.value[tool] = r.proxy
  } catch (e: any) { err.value = String(e) }
  finally { busy.value[tool] = false }
}

async function saveModels() {
  if (!hasUnsaved.value) return
  modelSaving.value = true
  modelSaved.value = false
  try {
    const m = Math.max(1, parseInt(String(mainRpm.value), 10) || 1)
    const s = Math.max(1, parseInt(String(subRpm.value), 10) || 1)
    const patch: any = {
      proxy_models: { [targetProxy.value]: { main: mainModel.value, sub: subModel.value } },
      proxy_rpm: { [targetProxy.value]: { main: m, sub: s } },
    }
    await api.patchConfig(patch)
    savedMain.value = mainModel.value
    savedSub.value = subModel.value
    savedMainRpm.value = m
    savedSubRpm.value = s
    mainRpm.value = m
    subRpm.value = s
    if (cfg.value) {
      const pm = ((cfg.value as any).proxy_models ||= {})
      pm[targetProxy.value] = { main: mainModel.value, sub: subModel.value }
      const pr = ((cfg.value as any).proxy_rpm ||= {})
      pr[targetProxy.value] = { main: m, sub: s }
    }
    modelSaved.value = true
    setTimeout(() => { modelSaved.value = false }, 2000)
  } catch (e: any) { err.value = String(e) }
  finally { modelSaving.value = false }
}

watch(targetProxy, () => {
  if (!cfg.value) return
  const cur = modelsForProxy(cfg.value, targetProxy.value)
  mainModel.value = cur.main
  subModel.value = cur.sub
  savedMain.value = cur.main
  savedSub.value = cur.sub
  const rpm = rpmForProxy(cfg.value, targetProxy.value)
  mainRpm.value = rpm.main
  subRpm.value = rpm.sub
  savedMainRpm.value = rpm.main
  savedSubRpm.value = rpm.sub
})

async function attemptStart(tool: ToolId) {
  if (tool === targetProxy.value && hasUnsaved.value) {
    const choice = window.confirm(t('sidebar.unsaved.body'))
    if (!choice) return
    await saveModels()
  }
  await showPreview(tool)
}

function badgeClass(s?: ProxyInfo | null) {
  if (!s) return ''
  if (s.status === 'running') return 'badge-running'
  if (s.status === 'external') return 'badge-external'
  return ''
}

function statusLabel(s?: ProxyInfo | null): string {
  const v = s?.status || 'offline'
  if (v === 'running') return t('setup.status.running')
  if (v === 'external') return t('setup.status.external')
  return t('setup.status.offline')
}

const showSubAgent = computed(() => targetProxy.value !== 'openai_compat')

onMounted(() => {
  loadAll()
  loadModels()
  timer = window.setInterval(loadAll, 5000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="flex gap-6">
    <aside class="shrink-0" :style="{ width: '240px' }">
      <div class="label mb-3">{{ t('sidebar.models') }}</div>

      <div class="mb-3">
        <div class="label mb-2">{{ t('sidebar.proxy') }}</div>
        <select class="input" v-model="targetProxy">
          <option value="claude">{{ t('sidebar.proxy.claude') }}</option>
          <option value="opencode">{{ t('sidebar.proxy.opencode') }}</option>
          <option value="openai_compat">{{ t('sidebar.proxy.openai_compat') }}</option>
        </select>
      </div>

      <div class="mb-3">
        <div class="label mb-2">{{ t('sidebar.main') }}</div>
        <select class="input" v-model="mainModel">
          <option v-for="m in models" :key="m.id" :value="m.id">{{ m.id }}</option>
        </select>
      </div>

      <div v-if="showSubAgent" class="mb-3">
        <div class="label mb-2">{{ t('sidebar.sub') }}</div>
        <select class="input" v-model="subModel">
          <option v-for="m in models" :key="m.id" :value="m.id">{{ m.id }}</option>
        </select>
      </div>

      <p v-if="!showSubAgent" class="mono text-[11px] mt-3 mb-3" :style="{ color: 'var(--text-muted)', lineHeight: 1.5 }">
        {{ t('sidebar.subagent.note') }}
      </p>

      <div class="mb-3">
        <div class="label mb-2">{{ t('sidebar.main.rpm') }}</div>
        <input class="input" type="number" min="1" v-model.number="mainRpm" />
      </div>

      <div v-if="showSubAgent" class="mb-3">
        <div class="label mb-2">{{ t('sidebar.sub.rpm') }}</div>
        <input class="input" type="number" min="1" v-model.number="subRpm" />
      </div>

      <p class="mono text-[11px] mt-1 mb-3" :style="{ color: 'var(--text-muted)', lineHeight: 1.5 }">
        {{ t('sidebar.rpm.note') }}
      </p>

      <div class="mt-3 flex items-center gap-2">
        <button class="btn btn-primary" :disabled="!hasUnsaved || modelSaving" @click="saveModels">
          {{ modelSaving ? t('sidebar.saving') : t('sidebar.save') }}
        </button>
        <span v-if="modelSaved" class="mono text-[11px]" :style="{ color: 'var(--accent)' }">{{ t('sidebar.saved') }}</span>
        <span v-else-if="hasUnsaved" class="mono text-[11px]" :style="{ color: 'var(--text-muted)' }">{{ t('sidebar.dirty') }}</span>
      </div>
    </aside>

    <div class="flex-1 min-w-0">
      <h1 class="h1 mb-2"><span class="hl">{{ t('setup.title') }}</span></h1>
      <p class="mb-6 text-[13px]" :style="{ color: 'var(--text-dim)', maxWidth: '640px' }">{{ t('setup.subtitle') }}</p>

      <section class="mb-6">
        <div class="label mb-2">{{ t('setup.section.key') }}</div>
        <div class="card flex items-end gap-3" :style="{ padding: '16px' }">
          <div class="flex-1">
            <div class="label mb-2">{{ t('setup.key.label') }}</div>
            <input class="input" type="password" v-model="keyInput"
                   :placeholder="cfg?.has_key ? '...' + (cfg.onlysq_key || '').slice(-6) : 'sq-...'" />
          </div>
          <button class="btn btn-primary" :disabled="saving || !keyInput.trim()" @click="saveKey">
            {{ saving ? t('setup.key.saving') : t('setup.key.save') }}
          </button>
        </div>
      </section>

      <section>
        <div class="label mb-2">{{ t('setup.section.tools') }}</div>
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div
            v-for="x in tools" :key="x.id"
            class="card" :class="{ 'is-running': statuses[x.id]?.status === 'running' }"
            :style="{ padding: '16px' }"
          >
            <div class="flex items-start justify-between mb-3">
              <div class="min-w-0">
                <div class="label mb-1">TOOL / {{ x.id.toUpperCase() }}</div>
                <div class="h2 text-[16px]">{{ x.title }}</div>
              </div>
              <span class="badge shrink-0" :class="badgeClass(statuses[x.id])">
                {{ statusLabel(statuses[x.id]) }}
              </span>
            </div>
            <p class="mb-4 text-[12px]" :style="{ color: 'var(--text-dim)', lineHeight: 1.5 }">{{ x.desc }}</p>
            <div class="flex items-center gap-2">
              <button
                v-if="statuses[x.id]?.status !== 'running'"
                class="btn btn-primary"
                :disabled="busy[x.id] || !cfg?.has_key"
                @click="attemptStart(x.id)"
              >{{ t('setup.btn.start') }}</button>
              <button
                v-else
                class="btn btn-ghost"
                :disabled="busy[x.id]"
                @click="stopTool(x.id)"
              >{{ t('setup.btn.stop') }}</button>
            </div>
          </div>
        </div>
      </section>

      <p class="mt-6 mono text-[12px]" :style="{ color: 'var(--text-dim)' }">
        {{ t('setup.help.q') }}
        <router-link to="/docs" :style="{ color: 'var(--accent)', textDecoration: 'underline' }">{{ t('setup.help.link') }}</router-link>
      </p>

      <p v-if="err" class="mt-4 mono text-[12px]" :style="{ color: 'var(--danger)' }">{{ err }}</p>
    </div>
  </div>

  <div
    v-if="preview"
    class="fixed inset-0 z-50 flex items-center justify-center"
    :style="{ background: 'rgba(0,0,0,0.7)' }"
    @click.self="preview = null"
  >
    <div class="card" :style="{ maxWidth: '720px', width: '90vw', maxHeight: '80vh', overflow: 'auto' }">
      <div class="label mb-2">PREVIEW / {{ preview.tool.toUpperCase() }}</div>
      <div class="h2 mb-4">{{ t('setup.preview.title') }}</div>
      <div class="label mb-1">{{ t('setup.preview.path') }}</div>
      <div class="mono mb-4 text-[13px]" :style="{ color: 'var(--text-dim)' }">{{ preview.data.target_path }}</div>
      <div class="label mb-1">{{ t('setup.preview.after') }}</div>
      <pre class="mono text-[12px] p-3 mb-6 overflow-auto"
           :style="{ background: 'var(--bg-elev-2)', border: '1px solid var(--border)', maxHeight: '40vh' }"
      >{{ preview.data.after }}</pre>
      <div class="flex justify-end gap-2">
        <button class="btn btn-ghost" @click="preview = null">{{ t('setup.preview.cancel') }}</button>
        <button class="btn btn-primary" @click="confirmStart(preview.tool)">{{ t('setup.preview.confirm') }}</button>
      </div>
    </div>
  </div>
</template>
