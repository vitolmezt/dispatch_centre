<script setup>
import { onMounted, ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { fetchRun, abortRun, stopRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel } from '../constants'
import { refreshTick, bump } from '../store/refresh'

const route = useRoute()
const run = ref(null)
const err = ref('')

async function load() {
  try {
    run.value = await fetchRun(route.params.id)
  } catch (e) {
    err.value = '加载失败: ' + (e?.message || e)
  }
}
onMounted(load)
// 其它页面发生动作后刷新一次
watch(refreshTick, () => load())

const isRunning = computed(() => run.value?.status === 'RUNNING')
// detach 后台进程启动即 SUCCEEDED，但进程仍常驻；只要有 PID 就允许“停止”
const canStop = computed(() => !!run.value?.pid)

async function onAbort() {
  if (!window.confirm('中止该运行?')) return
  await abortRun(run.value.id)
  bump() // 通知列表页刷新
  load()
}

async function onStop() {
  if (!window.confirm(`停止后台进程 (PID ${run.value.pid})？`)) return
  try {
    const r = await stopRun(run.value.id)
    if (r.stop) {
      alert('已发送停止指令')
    } else if (r.reason === 'no_pid') {
      alert('该记录没有记录 PID')
    } else {
      alert('进程可能已不存在或停止失败')
    }
  } catch (e) {
    alert('停止失败: ' + (e?.response?.data?.detail || e.message))
  }
  bump()
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
        <button class="stop" v-if="canStop" @click="onStop" title="按记录的 PID 用 taskkill 精准杀死后台进程">停止进程</button>
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
.stop {
  background: #1f3a5f; color: #fff; border: 1px solid #2f5f9f;
}
.stop:hover { background: #2a4d7a; }
.log pre { margin: 0; padding: 8px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
.log code { display: block; }
</style>