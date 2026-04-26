import { ref, computed } from 'vue'
import { en } from './en'
import { ru } from './ru'

export type Locale = 'en' | 'ru'
const dicts: Record<Locale, Record<string, string>> = { en, ru }

const saved = (() => { try { return localStorage.getItem('onlybridge-lang') as Locale | null } catch { return null } })()
export const locale = ref<Locale>(saved === 'ru' || saved === 'en' ? saved : 'en')

export function setLocale(l: Locale) {
  locale.value = l
  try { localStorage.setItem('onlybridge-lang', l) } catch {}
  document.documentElement.setAttribute('lang', l)
}

export function t(key: string, vars?: Record<string, string | number>): string {
  const dict = dicts[locale.value] || en
  let s = dict[key] ?? en[key] ?? key
  if (vars) for (const k in vars) s = s.replace(new RegExp(`\\{${k}\\}`, 'g'), String(vars[k]))
  return s
}

export const tr = computed(() => (key: string, vars?: Record<string, string | number>) => t(key, vars))
