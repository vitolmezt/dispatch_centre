<script setup>
import { onMounted, onBeforeUnmount, ref, provide } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { loadConstants } from './constants'
import { fetchJobs } from './api'
import { drawerState, closeJob } from './store/drawer'
import { connectRealtime, disconnectRealtime } from './store/realtime'
import JobDrawer from './components/JobDrawer.vue'
import RunDetailDrawer from './components/RunDetailDrawer.vue'
import ToastHost from './components/ToastHost.vue'

// 全局常量字典 + 菜单任务列表（供抽屉用）
const jobs = ref([])
provide('jobs', jobs)
provide('reloadJobs', reloadJobs)

async function reloadJobs() {
  try {
    const res = await fetchJobs({ all: true })
    jobs.value = Array.isArray(res) ? res : (res?.items || [])
  } catch (e) { /* ignore */ }
}

onMounted(() => {
  loadConstants().catch(() => {})
  reloadJobs()
  connectRealtime()   // 实时状态：任务开始/结束/中止时各看板自动刷新
})
// 应用关闭时断开 SSE
onBeforeUnmount(() => disconnectRealtime())

const route = useRoute()
const pageTitle = {
  '/jobs': '指令列表',
  '/running': '执行中心',
  '/history': '执行历史',
}

const menus = [
  { to: '/jobs', label: '指令列表' },
  { to: '/running', label: '执行中心' },
  { to: '/history', label: '执行历史' },
]
</script>

<template>
  <div class="admin">
    <!-- 左侧导航 -->
    <aside class="aside">
      <div class="logo"><span class="logo-mark">S</span>调度中心</div>
      <nav class="nav">
        <RouterLink v-for="m in menus" :key="m.to" :to="m.to" class="nav-item">
          <span>{{ m.label }}</span>
        </RouterLink>
      </nav>
    </aside>

    <!-- 右侧主区 -->
    <div class="main">
      <header class="topbar">
        <div class="page-title">{{ pageTitle[route.path] || '调度中心' }}</div>
        <div class="top-actions"></div>
      </header>

      <main class="content">
        <RouterView />
      </main>

      <footer class="app-footer">
        <span>调度中心 Scheduler Center</span>
        <span class="muted">© {{ new Date().getFullYear() }} · 任务调度看板</span>
      </footer>
    </div>

    <!-- 全局右侧抽屉：任务详情 / 执行详情 -->
    <JobDrawer v-if="drawerState.type === 'job'"
               :id="drawerState.selected" :jobs="jobs"
               @close="closeJob" @triggered="reloadJobs" />
    <RunDetailDrawer v-if="drawerState.type === 'run'"
                     :id="drawerState.selected"
                     @close="closeJob" />

    <!-- 全局动作结果提示（屏幕中心上方弹出） -->
    <ToastHost />
  </div>
</template>

<style scoped>
.admin { display: flex; min-height: 100vh; }

/* 左侧导航：管理系统常见深色窄栏 */
.aside {
  width: 220px; background: #171a21; border-right: 1px solid var(--border);
  display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh;
}
.logo {
  display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 17px;
  padding: 18px 16px; border-bottom: 1px solid var(--border); color: var(--text);
}
.logo-mark {
  width: 30px; height: 30px; border-radius: 7px;
  background: var(--primary); color: #fff; display: grid; place-items: center;
  font-weight: 800; letter-spacing: .5px;
}
.nav { padding: 12px 10px; display: flex; flex-direction: column; gap: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 12px; padding: 11px 14px;
  border-radius: 8px; color: #aab2c0; font-size: 14px;
}
.nav-item:hover { background: #21252e; color: var(--text); }
.nav-item.router-link-active { background: var(--primary); color: #fff; font-weight: 600; }
.n-icon { font-size: 15px; }

/* 右侧主区 */
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.topbar {
  height: 56px; display: flex; align-items: center; justify-content: space-between;
  padding: 0 22px; background: var(--panel); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 20;
}
.crumb { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.page-title { font-size: 15px; font-weight: 700; color: var(--text); }
.crumb-item { color: var(--muted); }
.crumb-item.active { color: var(--text); font-weight: 600; }
.crumb-sep { color: var(--border); }
.top-actions { display: flex; align-items: center; gap: 8px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); display: inline-block; }
.content { flex: 1; padding: 22px; }
.app-footer {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 22px; border-top: 1px solid var(--border);
  background: var(--panel); font-size: 12px; color: var(--text);
}
</style>