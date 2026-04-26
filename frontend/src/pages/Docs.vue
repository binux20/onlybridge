<script setup lang="ts">
import { t } from '../i18n'

const SITE = 'https://my.onlysq.ru'
const BOT = 'https://t.me/OnlySqVerificarion_bot'
const DOCS = 'https://docs.onlysq.ru/'

const tiers = [
  { id: 'email',    color: 'var(--danger)',    examples: 'docs.tiers.email.examples',    limits: ['0', '0', '0', '0', '0'] },
  { id: 'premium',  color: 'var(--accent)',    examples: 'docs.tiers.premium.examples',  limits: ['0', '0', '0', '3', '10'] },
  { id: 'plus',     color: 'var(--accent-hi)', examples: 'docs.tiers.plus.examples',     limits: ['0', '0', '3', '10', '20'] },
  { id: 'standard', color: 'var(--text)',      examples: 'docs.tiers.standard.examples', limits: ['0', '3', '10', '20', '40'] },
  { id: 'lite',     color: 'var(--text-dim)',  examples: 'docs.tiers.lite.examples',     limits: ['0', '10', '20', '40', '80'] },
  { id: 'paid',     color: 'var(--success)',   examples: 'docs.tiers.paid.examples',     limits: ['0', '0', '0', '0', '3'] },
]

const cols = [
  'docs.tiers.col.lvl_minus_1',
  'docs.tiers.col.lvl_0',
  'docs.tiers.col.lvl_1',
  'docs.tiers.col.lvl_2',
  'docs.tiers.col.lvl_3',
]
</script>

<template>
  <h1 class="h-display mb-4">{{ t('docs.title') }}</h1>
  <p class="mb-6" :style="{ color: 'var(--text-dim)', maxWidth: '720px' }">{{ t('docs.intro') }}</p>

  <section class="card mb-6" :style="{ padding: '16px 20px' }">
    <div class="label mb-2" :style="{ color: 'var(--accent)' }">{{ t('support.title') }}</div>
    <p class="text-[13px]" :style="{ color: 'var(--text)' }">
      {{ t('support.body') }}
      <a href="https://t.me/notgay8" target="_blank" rel="noopener" :style="{ color: 'var(--accent)' }" class="hover:underline ml-1">{{ t('support.handle') }}</a>
    </p>
  </section>

  <p class="mono text-[12px] mb-8" :style="{ color: 'var(--text-dim)', lineHeight: 1.5 }">
    <template v-for="(part, i) in t('docs.disclaimer').split('{link}')" :key="'top-'+i">
      <span>{{ part }}</span>
      <a v-if="i === 0" :href="DOCS" target="_blank" rel="noopener"
         :style="{ color: 'var(--accent)', textDecoration: 'underline' }">{{ t('docs.disclaimer.link') }}</a>
    </template>
  </p>

  <section class="card mb-6" :style="{ padding: '20px' }">
    <div class="label mb-3">{{ t('docs.key.title') }}</div>
    <div :style="{ color: 'var(--text)', lineHeight: 1.6, fontSize: '14px' }">
      <p class="mb-2">
        <template v-for="(p, i) in t('docs.key.s1').split('{site}')" :key="'s1-'+i">
          <span>{{ p }}</span>
          <a v-if="i === 0" :href="SITE" target="_blank" rel="noopener"
             :style="{ color: 'var(--accent)' }">my.onlysq.ru</a>
        </template>
      </p>
      <p class="mb-2">
        <template v-for="(p, i) in t('docs.key.s2').split('{bot}')" :key="'s2-'+i">
          <span>{{ p }}</span>
          <a v-if="i === 0" :href="BOT" target="_blank" rel="noopener"
             :style="{ color: 'var(--accent)' }">@OnlySqVerificarion_bot</a>
        </template>
      </p>
      <p class="mb-2">
        <template v-for="(p, i) in t('docs.key.s3').split('{docs}')" :key="'s3-'+i">
          <span>{{ p }}</span>
          <a v-if="i === 0" :href="DOCS" target="_blank" rel="noopener"
             :style="{ color: 'var(--accent)' }">{{ t('docs.key.docs_word') }}</a>
        </template>
      </p>
      <p class="mb-2">
        <template v-for="(p, i) in t('docs.key.s4').split('{site}')" :key="'s4-'+i">
          <span>{{ p }}</span>
          <a v-if="i === 0" :href="SITE" target="_blank" rel="noopener"
             :style="{ color: 'var(--accent)' }">my.onlysq.ru</a>
        </template>
      </p>
      <p>{{ t('docs.key.s5') }}</p>
    </div>
  </section>

  <section class="card mb-6" :style="{ padding: '20px' }">
    <div class="label mb-4">{{ t('docs.tiers.title') }}</div>
    <div class="overflow-x-auto">
      <table class="tier-table w-full mono text-[12px]" :style="{ borderCollapse: 'collapse' }">
        <thead>
          <tr :style="{ borderBottom: '1px solid var(--border)' }">
            <th class="text-left py-2 pr-4" :style="{ color: 'var(--text-muted)', fontWeight: 500 }">{{ t('docs.tiers.col.tier') }}</th>
            <th class="text-left py-2 pr-4" :style="{ color: 'var(--text-muted)', fontWeight: 500 }">{{ t('docs.tiers.col.examples') }}</th>
            <th v-for="c in cols" :key="c" class="text-right py-2 px-3"
                :style="{ color: 'var(--text-muted)', fontWeight: 500 }">{{ t(c) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tiers" :key="row.id"
              :style="{ borderBottom: '1px solid var(--border-soft)' }">
            <td class="py-2 pr-4 uppercase tracking-[0.05em]" :style="{ color: row.color }">{{ row.id }}</td>
            <td class="py-2 pr-4" :style="{ color: 'var(--text-dim)' }">{{ t(row.examples) }}</td>
            <td v-for="(v, i) in row.limits" :key="i" class="py-2 px-3 text-right"
                :style="{ color: i === 3 ? 'var(--accent)' : 'var(--text)' }">{{ v }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <div class="mb-8 mt-10">
    <div class="h2 mb-2">{{ t('docs.manual.title') }}</div>
    <p :style="{ color: 'var(--text-dim)', maxWidth: '720px', lineHeight: 1.6 }">{{ t('docs.manual.intro') }}</p>
  </div>

  <section class="card mb-6" :style="{ padding: '20px' }">
    <div class="label mb-2">{{ t('docs.claude.title') }}</div>
    <p :style="{ color: 'var(--text)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }">{{ t('docs.claude.body') }}</p>
  </section>

  <section class="card mb-6" :style="{ padding: '20px' }">
    <div class="label mb-2">{{ t('docs.opencode.title') }}</div>
    <p :style="{ color: 'var(--text)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }">{{ t('docs.opencode.body') }}</p>
  </section>

  <section class="card mb-6" :style="{ padding: '20px' }">
    <div class="label mb-2">{{ t('docs.openai_compat.title') }}</div>
    <p :style="{ color: 'var(--text)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }">{{ t('docs.openai_compat.body') }}</p>
  </section>

  <div class="mb-6 mt-10">
    <div class="h2 mb-2">{{ t('docs.stream.title') }}</div>
    <p :style="{ color: 'var(--text-dim)', maxWidth: '720px', lineHeight: 1.6 }">{{ t('docs.stream.intro') }}</p>
  </div>

  <section class="card mb-4" :style="{ padding: '20px' }">
    <div class="label mb-2" :style="{ color: 'var(--accent)' }">{{ t('docs.stream.realtime.title') }}</div>
    <p :style="{ color: 'var(--text)', lineHeight: 1.6 }">{{ t('docs.stream.realtime.body') }}</p>
  </section>

  <section class="card mb-4" :style="{ padding: '20px' }">
    <div class="label mb-2">{{ t('docs.stream.legacy.title') }}</div>
    <p :style="{ color: 'var(--text)', lineHeight: 1.6 }">{{ t('docs.stream.legacy.body') }}</p>
  </section>

  <p class="text-[12px] mb-6" :style="{ color: 'var(--text-dim)', lineHeight: 1.5 }">{{ t('docs.stream.claude.note') }}</p>

  <p class="mono text-[12px] mt-4" :style="{ color: 'var(--text-dim)', lineHeight: 1.5 }">
    <template v-for="(part, i) in t('docs.disclaimer').split('{link}')" :key="'bot-'+i">
      <span>{{ part }}</span>
      <a v-if="i === 0" :href="DOCS" target="_blank" rel="noopener"
         :style="{ color: 'var(--accent)', textDecoration: 'underline' }">{{ t('docs.disclaimer.link') }}</a>
    </template>
  </p>
</template>
