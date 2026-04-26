import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Setup from './pages/Setup.vue'
import Stats from './pages/Stats.vue'
import Logs from './pages/Logs.vue'
import Docs from './pages/Docs.vue'
import Settings from './pages/Settings.vue'
import './styles.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/setup' },
    { path: '/setup',    component: Setup,    meta: { idx: '01', tkey: 'tab.setup' } },
    { path: '/stats',    component: Stats,    meta: { idx: '02', tkey: 'tab.stats' } },
    { path: '/logs',     component: Logs,     meta: { idx: '03', tkey: 'tab.logs' } },
    { path: '/docs',     component: Docs,     meta: { idx: '04', tkey: 'tab.docs' } },
    { path: '/settings', component: Settings, meta: { idx: '05', tkey: 'tab.settings' } },
  ],
})

createApp(App).use(router).mount('#app')
