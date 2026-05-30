<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AppConfig } from '../api'
import { t, locale, setLocale, type Locale } from '../i18n'

const cfg = ref<AppConfig | null>(null)
const claudePath = ref('')
const opencodePath = ref('')
const streamMode = ref<'realtime' | 'legacy'>('realtime')
const saving = ref(false)
const err = ref('')
const saved = ref(false)
const bindHost = ref('127.0.0.1')
const hasAuthToken = ref(false)
const authTokenMasked = ref('')
const newTokenReveal = ref('')
const netSaving = ref(false)
const netSaved = ref(false)
const netError = ref('')

async function load() {
  try {
    const c = await api.getConfig()
    cfg.value = c
    claudePath.value = c.tool_paths?.claude || ''
    opencodePath.value = c.tool_paths?.opencode || ''
    streamMode.value = c.stream_mode === 'legacy' ? 'legacy' : 'realtime'
    bindHost.value = (c.bind_host || '127.0.0.1').trim() || '127.0.0.1'
    hasAuthToken.value = !!c.has_auth_token
    authTokenMasked.value = c.bridge_auth_token || ''
  } catch (e: any) { err.value = String(e) }
}

async function saveNetwork() {
  netSaving.value = true
  netSaved.value = false
  netError.value = ''
  const host = bindHost.value.trim() || '127.0.0.1'
  if (host !== '127.0.0.1' && !hasAuthToken.value) {
    netError.value = t('settings.net.error.token_required')
    netSaving.value = false
    return
  }
  try {
    const r = await api.patchConfig({ bind_host: host } as any)
    netSaved.value = true
    if ((r as any).restart_required) netSaved.value = true
    setTimeout(() => { netSaved.value = false }, 3000)
  } catch (e: any) { netError.value = String(e) }
  finally { netSaving.value = false }
}

async function regenerateToken() {
  if (!confirm(t('settings.net.regen.confirm'))) return
  netSaving.value = true
  netError.value = ''
  newTokenReveal.value = ''
  try {
    const r = await api.regenerateToken()
    newTokenReveal.value = r.bridge_auth_token
    hasAuthToken.value = true
    try { localStorage.setItem('onlybridge_auth_token', r.bridge_auth_token) } catch {}
  } catch (e: any) { netError.value = String(e) }
  finally { netSaving.value = false }
}

async function copyToken() {
  if (!newTokenReveal.value) return
  try { await navigator.clipboard.writeText(newTokenReveal.value) } catch {}
}

async function setStreamMode(m: 'realtime' | 'legacy') {
  streamMode.value = m
  try { await api.patchConfig({ stream_mode: m } as any) } catch (e: any) { err.value = String(e) }
}

async function savePaths() {
  saving.value = true
  saved.value = false
  try {
    await api.patchConfig({
      tool_paths: {
        claude: claudePath.value.trim(),
        opencode: opencodePath.value.trim(),
      },
    } as any)
    saved.value = true
    setTimeout(() => { saved.value = false }, 2000)
  } catch (e: any) { err.value = String(e) }
  finally { saving.value = false }
}

function switchLang(l: Locale) {
  setLocale(l)
  api.patchConfig({ lang: l } as any).catch(() => {})
}

onMounted(load)
</script>

<template>
  <h1 class="h-display mb-8">{{ t('settings.title') }}</h1>

  <section class="card mb-4" :style="{ padding: '16px' }">
    <div class="label mb-3">{{ t('settings.lang') }}</div>
    <div class="flex gap-2">
      <button class="btn" :class="locale === 'en' ? 'btn-primary' : 'btn-ghost'" @click="switchLang('en')">{{ t('settings.lang.en') }}</button>
      <button class="btn" :class="locale === 'ru' ? 'btn-primary' : 'btn-ghost'" @click="switchLang('ru')">{{ t('settings.lang.ru') }}</button>
    </div>
  </section>

  <section class="card mb-6">
    <div class="label mb-4">{{ t('settings.paths') }}</div>
    <div class="mb-4">
      <div class="label mb-2">{{ t('settings.paths.claude') }}</div>
      <input class="input" v-model="claudePath" :placeholder="t('settings.paths.placeholder')" />
    </div>
    <div class="mb-4">
      <div class="label mb-2">{{ t('settings.paths.opencode') }}</div>
      <input class="input" v-model="opencodePath" :placeholder="t('settings.paths.placeholder')" />
    </div>
    <div class="flex items-center gap-3">
      <button class="btn btn-primary" :disabled="saving" @click="savePaths">{{ saving ? '...' : t('settings.paths.save') }}</button>
      <span v-if="saved" class="mono text-[12px]" :style="{ color: 'var(--accent)' }">OK</span>
    </div>
  </section>

  <section class="card mb-6">
    <div class="label mb-4">{{ t('settings.stream.title') }}</div>
    <div class="flex gap-2 mb-3">
      <button class="btn" :class="streamMode === 'realtime' ? 'btn-primary' : 'btn-ghost'" @click="setStreamMode('realtime')">{{ t('settings.stream.realtime') }}</button>
      <button class="btn" :class="streamMode === 'legacy' ? 'btn-primary' : 'btn-ghost'" @click="setStreamMode('legacy')">{{ t('settings.stream.legacy') }}</button>
    </div>
    <p class="text-[12px] mb-2" :style="{ color: 'var(--text-dim)' }">
      {{ streamMode === 'realtime' ? t('settings.stream.realtime.desc') : t('settings.stream.legacy.desc') }}
    </p>
    <p class="text-[11px]" :style="{ color: 'var(--text-dim)' }">{{ t('settings.stream.note') }}</p>
  </section>

  <section class="card mb-6">
    <div class="label mb-2">{{ t('settings.subagent.title') }}</div>
    <p :style="{ color: 'var(--text-dim)' }">{{ t('settings.subagent.body') }}</p>
  </section>

  <section class="card mb-6">
    <div class="label mb-4" :style="{ color: 'var(--accent)' }">{{ t('settings.net.title') }}</div>
    <p class="text-[12px] mb-4" :style="{ color: 'var(--text-dim)', lineHeight: 1.5 }">{{ t('settings.net.body') }}</p>

    <div class="mb-4">
      <div class="label mb-2">{{ t('settings.net.bind') }}</div>
      <input class="input" v-model="bindHost" placeholder="127.0.0.1" />
      <p class="mono text-[11px] mt-2" :style="{ color: 'var(--text-muted)', lineHeight: 1.5 }">{{ t('settings.net.bind.hint') }}</p>
    </div>

    <div class="mb-4">
      <div class="label mb-2">{{ t('settings.net.token') }}</div>
      <div v-if="hasAuthToken" class="mono text-[12px] mb-2" :style="{ color: 'var(--text-dim)' }">{{ authTokenMasked || t('settings.net.token.set') }}</div>
      <div v-else class="mono text-[12px] mb-2" :style="{ color: 'var(--text-muted)' }">{{ t('settings.net.token.unset') }}</div>
      <button class="btn btn-ghost" :disabled="netSaving" @click="regenerateToken">{{ t('settings.net.regen') }}</button>
      <div v-if="newTokenReveal" class="mt-3 p-3"
           :style="{ background: 'var(--bg-elev-2)', border: '1px solid var(--accent)' }">
        <div class="label mb-2" :style="{ color: 'var(--accent)' }">{{ t('settings.net.regen.new') }}</div>
        <div class="mono text-[13px] mb-2" :style="{ color: 'var(--text)', wordBreak: 'break-all' }">{{ newTokenReveal }}</div>
        <button class="btn btn-ghost" @click="copyToken">{{ t('settings.net.copy') }}</button>
        <p class="text-[11px] mt-2" :style="{ color: 'var(--text-dim)' }">{{ t('settings.net.regen.save_now') }}</p>
      </div>
    </div>

    <div class="flex items-center gap-3">
      <button class="btn btn-primary" :disabled="netSaving" @click="saveNetwork">{{ netSaving ? '...' : t('settings.net.save') }}</button>
      <span v-if="netSaved" class="mono text-[12px]" :style="{ color: 'var(--accent)' }">{{ t('settings.net.restart_required') }}</span>
    </div>
    <p v-if="netError" class="mono text-[12px] mt-3" :style="{ color: 'var(--danger)' }">{{ netError }}</p>
    <p class="text-[11px] mt-3" :style="{ color: 'var(--danger)', lineHeight: 1.5 }">{{ t('settings.net.warn') }}</p>
  </section>

  <section class="card">
    <div class="label mb-2" :style="{ color: 'var(--accent)' }">{{ t('support.title') }}</div>
    <p class="text-[13px]" :style="{ color: 'var(--text)' }">
      {{ t('support.body') }}
      <a href="https://t.me/notgay8" target="_blank" rel="noopener" :style="{ color: 'var(--accent)' }" class="hover:underline ml-1">{{ t('support.handle') }}</a>
    </p>
  </section>

  <p v-if="err" class="mt-6 mono text-[12px]" :style="{ color: 'var(--danger)' }">{{ err }}</p>
</template>
