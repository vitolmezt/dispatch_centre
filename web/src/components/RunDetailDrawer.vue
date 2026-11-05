<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { fetchRun, abortRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel } from '../constants'
import { bump } from '../store/refresh'
import { toast } from '../store/toast'

/**
 * 全局右侧抽屉（约半屏宽）：展示一条执行记录的详情。
 *  - 基本信息（任务名 / 类型 / 触发 / PID / 开始 / 结束 / 耗时 / 退出码 / 错误）
 *  - 运行日志（实时）
 * 点击抽屉外的遮罩或 × 号均可关闭。
 */

const props = defineProps({
  id: String,                 // run id
})
const emit = defineEmits(['close'])

const open = ref(false)
const run = ref(null)
const err = ref('')

watch(() => props.id, (v) => {
  if (v) { open.value = true; load(v) }
  else { open.value = false }
}, { immediate: true })

async function load(runId = props.id) {
  run.value = null
  err.value = ''
  if (!runId) return
  try {
    run.value = await fetchRun(runId)
  } catch (e) {
    err.value = '加载失败: ' + (e?.message || e)
  }
}

const isRunning = computed(() => run.value?.status === 'RUNNING')

async function onAbort() {
  try {
    await abortRun(run.value.id)
    toast.info(`已发送中止请求「${run.value.job_name}」`)
    bump() // 通知列表页刷新
    load()
  } catch (e) {
    toast.error('中止失败: ' + (e?.response?.data?.detail || e.message))
  }
}
</script>

<template>
  <teleport to="body">
    <transition name="drawer">
      <div v-if="open" class="drawer-root">
        <div class="backdrop" @click="open = false; $emit('close')"></div>
        <aside class="drawer">
          <header class="d-head">
            <div class="d-title">
              <span class="d-name">{{ run?.job_name || '执行详情' }}</span>
              <span v-if="run" :class="['badge', run.status]">{{ statusLabel(run.status) }}</span>
            </div>
            <button class="icon-btn" @click="open = false; $emit('close')">✕</button>
          </header>

          <div v-if="run" class="d-body">
            <div class="meta">
              <div class="m-item"><label>Run ID</label><span class="mono">{{ run.id }}</span></div>
              <div class="m-item"><label>类型</label><span class="badge NEUTRAL">{{ jobTypeLabel(run.job_type) }}</span></div>
              <div class="m-item"><label>触发</label><span>{{ triggerLabel(run.trigger) }}</span></div>
              <div class="m-item"><label>PID</label><span class="mono">{{ run.pid ?? '—' }}</span></div>
              <div class="m-item"><label>开始</label><span class="mono">{{ run.start || '—' }}</span></div>
              <div class="m-item"><label>结束</label><span class="mono">{{ run.end || '—' }}</span></div>
              <div class="m-item"><label>耗时</label><span class="mono">{{ run.duration_ms != null ? (run.duration_ms/1000).toFixed(2)+'s' : '—' }}</span></div>
              <div class="m-item"><label>退出码</label><span class="mono">{{ run.exit_code ?? '—' }}</span></div>
              <div class="m-item" v-if="run.error"><label>错误</label><span style="color:var(--danger)">{{ run.error }}</span></div>
            </div>

            <h4>运行日志</h4>
            <div class="log">
              <pre v-if="run.log?.length"><code v-for="(l, i) in run.log" :key="i">{{ l }}</code></pre>
              <div v-else class="muted">暂无日志输出</div>
            </div>

            <div class="d-actions" v-if="isRunning">
              <button class="danger" @click="onAbort">中止</button>
            </div>
          </div>

          <div v-else class="d-body muted">{{ err || '加载中…' }}</div>
        </aside>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.drawer-root { position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 1000; }
.backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.35); }
.drawer {
  position: absolute; top: 0; right: 0; height: 100%;
  width: 50vw; background: var(--panel);
  border-left: 1px solid var(--border);
  display: flex; flex-direction: column; box-shadow: -8px 0 24px rgba(0,0,0,.35);
}
.d-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--border);
}
.d-title { display: flex; align-items: center; gap: 10px; }
.d-name { font-size: 18px; font-weight: 700; }
.icon-btn { background: transparent; border: none; color: var(--muted); font-size: 16px; padding: 4px 8px; border-radius: 6px; }
.icon-btn:hover { color: var(--text); background: var(--panel-2); }
.d-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
h4 { margin: 0 0 10px; font-size: 12px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
.meta { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 4px 20px; margin-bottom: 20px; }
.m-item { display: flex; flex-direction: column; gap: 4px; padding: 6px 0; }
.m-item label { font-size: 12px; color: var(--muted); }
.m-item span { font-size: 13px; word-break: break-all; }
.log { background: #0b0d10; font-family: var(--mono); font-size: 12px; max-height: 55vh; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.log pre { margin: 0; padding: 8px 10px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.log code { display: block; font-size: 12px; line-height: 1.55; }
.d-actions { padding-top: 14px; }
button.danger { color: var(--danger); border-color: #5a2a2c; }

.drawer-enter-active, .drawer-leave-active { transition: opacity .22s ease; }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-active .drawer, .drawer-leave-active .drawer { transition: transform .22s ease; }
.drawer-enter-from .drawer, .drawer-leave-to .drawer { transform: translateX(100%); }
.drawer-enter-active .backdrop, .drawer-leave-active .backdrop { transition: opacity .22s ease; }
.drawer-enter-from .backdrop, .drawer-leave-to .backdrop { opacity: 0; }
</style>