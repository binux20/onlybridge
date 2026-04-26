<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AppConfig } from '../api'
import { t, locale, setLocale, type Locale } from '../i18n'

const cfg = ref<AppConfig | null>(null)
const claudePath = ref('')
const opencodePath = ref('')
const saving = ref(false)
const err = ref('')
const saved = ref(false)

async function load() {
  try {
    const c = await api.getConfig()
    cfg.value = c
    claudePath.value = c.tool_paths?.claude || ''
    opencodePath.value = c.tool_paths?.opencode || ''
  } catch (e: any) { err.value = String(e) }
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
    <div class="label mb-2">{{ t('settings.subagent.title') }}</div>
    <p :style="{ color: 'var(--text-dim)' }">{{ t('settings.subagent.body') }}</p>
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
