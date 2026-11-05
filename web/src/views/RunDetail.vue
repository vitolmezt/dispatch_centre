<script setup>
import { onMounted, ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { fetchRun, abortRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel } from '../constants'
import { refreshTick, bump } from '../store/refresh'
import { toast } from '../store/toast'

const route = useRoute()
const run = ref(null)
const err = ref('')
let timer = null

async function load() {
  try {
    run.value = await fetchRun(route.params.id)
  } catch (e) {
    err.value = '加载失败: ' + (e?.message || e)
  }
  refreshTimer()
}
onMounted(load)
// 其它页面发生动作后刷新一次
watch(refreshTick, () => load())
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function refreshTimer() {
  clearInterval(timer)
  if (run.value?.status === 'RUNNING') {
    timer = setInterval(async () => {
      try { run.value = await fetchRun(route.params.id) } catch (e) { /* keep last */ }
    }, 2500)
  }
}

const isRunning = computed(() => run.value?.status === 'RUNNING')

async function onAbort() {
  try {
    await abortRun(run.value.id)
    // 不在本地弹提示，后端中止成功的 finished 事件会经 SSE 弹出「已中止」
  } catch (e) {
    toast.error('中止失败: ' + (e?.response?.data?.detail || e.message))
  }
  bump() // 通知列表页刷新
  load()
}
</script>

<template>
  <div v-if="run">
    <div class="row" style="justify-content: space-between; margin-bottom:16px">
      <h2 style="margin:0">{{ run.job_name }} <span class="muted" style="font-weight:400">/ {{ run.id }}</span></h2>
      <div class="row">
        <span :class="['badge', run.status]">{{ statusLabel(run.status) }}</span>
        <button class="danger" v-if="isRunning" @click="onAbort">中止</button>
      </div>
    </div>

    <div class="panel meta">
      <div class="m-item"><label>类型</label><span class="badge NEUTRAL">{{ jobTypeLabel(run.job_type) }}</span></div>
      <div class="m-item"><label>触发</label><span>{{ triggerLabel(run.trigger) }}</span></div>
      <div class="m-item"><label>PID</label><span class="mono">{{ run.pid ?? '—' }}</span></div>
      <div class="m-item"><label>开始</label><span class="mono">{{ run.start || '—' }}</span></div>
      <div class="m-item"><label>结束</label><span class="mono">{{ run.end || '—' }}</span></div>
      <div class="m-item"><label>耗时</label><span class="mono">{{ run.duration_ms != null ? (run.duration_ms/1000).toFixed(2)+'s' : '—' }}</span></div>
      <div class="m-item"><label>退出码</label><span class="mono">{{ run.exit_code ?? '—' }}</span></div>
      <div class="m-item" v-if="run.error"><label>错误</label><span style="color:var(--danger)">{{ run.error }}</span></div>
    </div>

    <h3 style="margin:20px 0 10px">运行日志</h3>
    <div class="panel log">
      <pre v-if="run.log?.length"><code v-for="(l, i) in run.log" :key="i">{{ l }}</code></pre>
      <div v-else class="muted">暂无日志输出</div>
    </div>
  </div>
  <div v-else>
    <div class="muted">{{ err || '加载中…' }}</div>
  </div>
</template>

<style scoped>
.meta { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 4px 20px; }
.m-item { display: flex; flex-direction: column; gap: 4px; padding: 6px 0; }
.m-item label { font-size: 12px; color: var(--muted); }
.log { background: #0b0d10; font-family: var(--mono); max-height: 60vh; overflow: auto; }
.log pre { margin: 0; padding: 8px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
.log code { display: block; }
</style>