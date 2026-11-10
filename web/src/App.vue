<script setup>
import { onMounted, onBeforeUnmount, ref, provide } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { loadConstants } from './constants'
import { fetchJobs, logout } from './api'
import { getToken, clearAuth } from './auth'
import { drawerState, closeJob } from './store/drawer'
import { connectRealtime, disconnectRealtime } from './store/realtime'
import { initTheme, useTheme, toggleTheme } from './composables/useTheme'
import JobDrawer from './components/JobDrawer.vue'
import RunDetailDrawer from './components/RunDetailDrawer.vue'

const { theme } = useTheme()

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

const router = useRouter()
const route = useRoute()
const loggedIn = ref(!!getToken())

// 会话失效（401）→ 清理本地 token + 断开 SSE → 回登录页
function handleUnauthorized() {
  clearAuth()
  disconnectRealtime()
  loggedIn.value = false
  if (router.currentRoute.value.path !== '/login') router.replace('/login')
}
document.addEventListener('sc:unauthorized', handleUnauthorized)
// 登录成功 → 建立实时连接并切换登录态（路由跳转不会重新挂载 App）
function handleAuthenticated() {
  loggedIn.value = true
  loadConstants().catch(() => {})
  reloadJobs()
  connectRealtime()
}
document.addEventListener('sc:authenticated', handleAuthenticated)

async function doLogout() {
  try { await logout() } catch (e) { /* 后端已失效也无妨 */ }
  clearAuth()
  disconnectRealtime()
  loggedIn.value = false
  router.replace('/login')
  ElMessage.info('已退出登录')
}

onMounted(() => {
  initTheme()
  loadConstants().catch(() => {})
  reloadJobs()
  if (getToken()) connectRealtime()
  loggedIn.value = !!getToken()
})
onBeforeUnmount(() => {
  disconnectRealtime()
  document.removeEventListener('sc:unauthorized', handleUnauthorized)
  document.removeEventListener('sc:authenticated', handleAuthenticated)
})

const pageTitle = {
  '/dashboard': '数据看板',
  '/jobs': '任务列表',
  '/running': '执行中心',
  '/history': '执行历史',
  '/service-groups': '服务组',
}

const menus = [
  { to: '/dashboard', label: '数据看板' },
  { to: '/jobs', label: '任务列表' },
  { to: '/running', label: '执行中心' },
  { to: '/history', label: '执行历史' },
  { to: '/service-groups', label: '服务组' },
]
</script>

<template>
  <div class="admin">
    <el-container class="app-shell">
      <el-aside width="220px" class="aside">
        <div class="logo"><span class="logo-mark">S</span>调度中心</div>
        <nav class="nav">
          <RouterLink v-for="m in menus" :key="m.to" :to="m.to" class="nav-item">
            <span>{{ m.label }}</span>
          </RouterLink>
        </nav>
      </el-aside>

      <el-container class="main-layout">
        <el-header class="topbar" height="56px">
          <div class="page-title">{{ pageTitle[route.path] || '调度中心' }}</div>
          <div class="top-actions">
            <el-tooltip :content="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'" placement="bottom" :show-after="200">
              <button class="theme-toggle" type="button" @click="toggleTheme()" :title="theme === 'dark' ? '浅色' : '深色'">
                <svg v-if="theme === 'dark'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="theme-icon">
                  <circle cx="12" cy="12" r="5" />
                  <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
                  <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                  <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
                  <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                </svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="theme-icon">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
              </button>
            </el-tooltip>
            <el-button v-if="loggedIn" size="small" text @click="doLogout">退出登录</el-button>
          </div>
        </el-header>

        <el-main class="content">
          <RouterView />
        </el-main>

        <footer class="app-footer">
          <span>调度中心 Scheduler Center</span>
          <span class="muted">© {{ new Date().getFullYear() }} · 任务调度看板</span>
        </footer>
      </el-container>
    </el-container>

    <JobDrawer v-if="drawerState.type === 'job'"
               :id="drawerState.selected" :jobs="jobs"
               @close="closeJob" @triggered="reloadJobs" />
    <RunDetailDrawer v-if="drawerState.type === 'run'"
                     :id="drawerState.selected"
                     @close="closeJob" />
  </div>
</template>

<style scoped>
.admin, .app-shell { height: 100vh; }
.app-shell { display: flex; overflow: hidden; }
.main-layout {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.aside {
  background: var(--bg-sidebar); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; flex-shrink: 0;
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
  border-radius: 8px; color: var(--text-secondary); font-size: 14px; text-decoration: none;
  transition: background .15s, color .15s;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text); }
.nav-item.router-link-active { background: var(--primary); color: #fff; font-weight: 600; }

.topbar {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 22px; background: var(--panel); border-bottom: 1px solid var(--border);
}
.page-title { font-size: 15px; font-weight: 700; color: var(--text); }
.top-actions { display: flex; align-items: center; gap: 8px; }

.theme-toggle {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border-radius: 8px;
  border: 1px solid var(--border); background: transparent; cursor: pointer;
  color: var(--text-secondary);
  transition: background .15s, color .15s, border-color .15s;
}
.theme-toggle:hover { background: var(--bg-hover); color: var(--text); border-color: var(--border-strong); }
.theme-icon { width: 17px; height: 17px; }

.content {
  flex: 1; min-height: 0; overflow: hidden;
  padding: 22px; background: var(--bg);
}

.app-footer {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 22px; border-top: 1px solid var(--border);
  background: var(--panel); font-size: 12px; color: var(--text);
}
</style>