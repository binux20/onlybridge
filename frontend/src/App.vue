<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { locale, setLocale, t } from './i18n'
import { setUnauthorizedHandler, setToken, getToken } from './api'

const route = useRoute()
const theme = ref<'dark' | 'light'>('dark')
const loginNeeded = ref(false)
const loginInput = ref('')
const loginErr = ref('')

function submitLogin() {
  const v = loginInput.value.trim()
  if (!v) { loginErr.value = t('login.empty'); return }
  setToken(v)
  loginInput.value = ''
  loginErr.value = ''
  loginNeeded.value = false
  location.reload()
}

function logout() {
  setToken('')
  loginNeeded.value = true
}

function setTheme(next: 'dark' | 'light') {
  theme.value = next
  document.documentElement.setAttribute('data-theme', next)
  try { localStorage.setItem('onlybridge-theme', next) } catch {}
}

onMounted(() => {
  const saved = (() => { try { return localStorage.getItem('onlybridge-theme') } catch { return null } })()
  if (saved === 'dark' || saved === 'light') setTheme(saved)
  else setTheme('dark')
  document.documentElement.setAttribute('lang', locale.value)
  setUnauthorizedHandler(() => { loginNeeded.value = true })
})

const hasToken = computed(() => !!getToken())

const tabs = computed(() => [
  { path: '/setup',    idx: '01', label: t('tab.setup') },
  { path: '/stats',    idx: '02', label: t('tab.stats') },
  { path: '/logs',     idx: '03', label: t('tab.logs') },
  { path: '/docs',     idx: '04', label: t('tab.docs') },
  { path: '/settings', idx: '05', label: t('tab.settings') },
])

const routeIdx = computed(() => (route.meta.idx as string) || '00')
const routeLabel = computed(() => {
  const k = route.meta.tkey as string | undefined
  return k ? t(k) : 'OnlyBridge'
})
</script>

<template>
  <div class="relative min-h-full">
    <header class="relative z-10 border-b" :style="{ borderColor: 'var(--border)' }">
      <div class="max-w-[1280px] mx-auto px-12 h-14 flex items-center justify-between">
        <div class="flex items-center gap-8">
          <router-link to="/setup" class="flex items-center gap-3">
            <span class="inline-flex items-center justify-center w-8 h-8 mono font-semibold text-[14px]"
                  :style="{ background: 'var(--accent)', color: '#0a0a0a' }">Sq</span>
            <span class="mono uppercase text-[14px] tracking-[0.08em]">{{ t('app.name') }}</span>
          </router-link>
          <nav class="flex items-center gap-6">
            <router-link
              v-for="x in tabs" :key="x.path" :to="x.path"
              custom v-slot="{ navigate, isActive }"
            >
              <button class="tab" :class="{ active: isActive }" @click="navigate">
                <span :style="{ color: 'var(--text-muted)' }">{{ x.idx }}</span>
                <span class="mx-2" :style="{ color: 'var(--text-muted)' }">/</span>
                {{ x.label }}
              </button>
            </router-link>
          </nav>
        </div>
        <div class="flex items-center gap-2">
          <div class="flex items-center" :style="{ border: '1px solid var(--border)', borderRadius: '2px' }">
            <button class="btn btn-ghost h-8 border-0"
                    :style="{ color: locale === 'en' ? 'var(--text)' : 'var(--text-muted)', borderRight: '1px solid var(--border)' }"
                    @click="setLocale('en')">EN</button>
            <button class="btn btn-ghost h-8 border-0"
                    :style="{ color: locale === 'ru' ? 'var(--text)' : 'var(--text-muted)' }"
                    @click="setLocale('ru')">RU</button>
          </div>
          <button class="btn btn-ghost h-8" @click="setTheme(theme === 'dark' ? 'light' : 'dark')">
            {{ theme === 'dark' ? t('topbar.theme.light') : t('topbar.theme.dark') }}
          </button>
          <button v-if="hasToken" class="btn btn-ghost h-8" @click="logout">{{ t('topbar.logout') }}</button>
        </div>
      </div>
    </header>

    <main class="relative z-10 max-w-[1280px] mx-auto px-12 py-12">
      <div class="label mb-3">
        <span :style="{ color: 'var(--text)' }">{{ routeIdx }}</span>
        <span class="mx-2">/</span>
        <span>{{ routeLabel }}</span>
      </div>
      <router-view />
    </main>

    <div v-if="loginNeeded" class="fixed inset-0 z-50 flex items-center justify-center"
         :style="{ background: 'rgba(0,0,0,0.85)' }">
      <div class="card" :style="{ maxWidth: '440px', width: '90vw', padding: '24px' }">
        <div class="label mb-2" :style="{ color: 'var(--accent)' }">{{ t('login.title') }}</div>
        <p class="text-[13px] mb-4" :style="{ color: 'var(--text-dim)', lineHeight: 1.5 }">{{ t('login.body') }}</p>
        <input class="input mb-3" type="password" v-model="loginInput"
               :placeholder="t('login.placeholder')" @keyup.enter="submitLogin" autofocus />
        <p v-if="loginErr" class="mono text-[12px] mb-3" :style="{ color: 'var(--danger)' }">{{ loginErr }}</p>
        <button class="btn btn-primary w-full" @click="submitLogin">{{ t('login.submit') }}</button>
        <p class="mono text-[11px] mt-3" :style="{ color: 'var(--text-muted)', lineHeight: 1.5 }">{{ t('login.hint') }}</p>
      </div>
    </div>
  </div>
</template>
