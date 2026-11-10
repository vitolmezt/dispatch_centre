<script setup>
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue'
import { fetchRun, streamRunLog } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel } from '../constants'
import { refreshTick } from '../store/refresh'


const props = defineProps({
  id: String,
})
const emit = defineEmits(['close'])

const open = ref(false)
const run = ref(null)
const err = ref('')
let timer = null
let stream = null
let isStreaming = false

const logEl = ref(null)
const stickToBottom = ref(true)

function close() { open.value = false; emit('close') }

watch(() => props.id, (v) => {
  if (v) { open.value = true; load(v) }
  else { open.value = false }
}, { immediate: true })

const metaItems = computed(() => {
  const r = run.value
  if (!r) return []
  const items = [
    { label: '类型', value: jobTypeLabel(r.job_type) },
    { label: '触发', value: triggerLabel(r.trigger) },
    { label: 'PID', value: String(r.pid ?? '—') },
    { label: '开始', value: r.start || '—' },
    { label: '结束', value: r.end || '—' },
    { label: '耗时', value: r.duration_ms != null ? (r.duration_ms / 1000).toFixed(2) + 's' : '—' },
    { label: '退出码', value: String(r.exit_code ?? '—') },
  ]
  if (r.error) items.push({ label: '错误', value: r.error })
  if (r.node_id) items.push({ label: '节点', value: r.node_id })
  if (r.job_type !== 'FILEOP') {
    items.push({ label: '进程独立', value: r.detached ? '是 (detach 常驻)' : '否' })
    if (r.process_group_id != null) items.push({ label: '进程树根 PID', value: String(r.process_group_id) })
    if (r.executable_path) items.push({ label: '可执行文件', value: r.executable_path })
    if (r.command_line_fingerprint) items.push({ label: '命令指纹', value: r.command_line_fingerprint })
    if (r.last_heartbeat) items.push({ label: '最近心跳', value: String(r.last_heartbeat).replace('T', ' ').slice(0, 19) })
    if (r.adopted) items.push({ label: '接管状态', value: '已接管 (ORPHANED → RUNNING)' })
  }
  return items
})

const interruptNotice = computed(() => {
  const r = run.value
  if (!r) return ''
  const abnormal = ['ORPHANED', 'UNKNOWN', 'INTERRUPTED']
  if (!abnormal.includes(String(r.status).toUpperCase())) return ''
  return r.interrupted_reason || r.error || statusLabel(r.status)
})

function onScroll() {
  const el = logEl.value
  if (!el) return
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

async function scrollBottom() {
  await nextTick()
  const el = logEl.value
  if (el && stickToBottom.value) el.scrollTop = el.scrollHeight
}

async function load(runId = props.id) {
  err.value = ''
  stopStream()
  if (!runId) return
  try {
    run.value = await fetchRun(runId)
    if (run.value?.status === 'RUNNING') startStream(runId)
    else await scrollBottom()
  } catch (e) {
    err.value = '加载失败: ' + (e?.message || e)
  }
}

function startStream(runId) {
  if (isStreaming || !run.value || run.value.status !== 'RUNNING') return
  isStreaming = true
  stream = streamRunLog(runId, {
    onLine: (line) => {
      run.value.log = run.value.log || []
      run.value.log.push(line)
      if (run.value.log.length > 2000) run.value.log = run.value.log.slice(-2000)
      void scrollBottom()
    },
    onEnd: async () => {
      isStreaming = false
      stream = null
      try { run.value = await fetchRun(runId) } catch (e) { /* keep */ }
      await scrollBottom()
    },
  })
}

watch(refreshTick, async () => {
  if (!open.value || !run.value) return
  const cached = run.value
  try {
    const fresh = await fetchRun(props.id)
    fresh.log = cached.log
    run.value = fresh
  } catch (e) { /* keep */ }
})

function stopStream() {
  if (stream) { try { stream.close() } catch (e) { /* ignore */ } }
  stream = null
  isStreaming = false
}

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  stopStream()
})
</script>

<template>
  <teleport to="body">
    <el-drawer :model-value="open" size="80%" :show-close="true"
               :with-header="true" :close-on-click-modal="true" append-to-body @close="close">
      <template #header>
        <div class="d-head">
          <div class="d-title">
            <span class="d-name">{{ run?.job_name || '执行详情' }}</span>
            <span v-if="run" :class="['badge', run.status]">{{ statusLabel(run.status) }}</span>
          </div>
        </div>
      </template>

      <div v-if="run" class="d-body">
        <div class="meta">
          <div v-for="m in metaItems" :key="m.label" class="m-item" :title="m.value">
            <label>{{ m.label }}</label>
            <span class="mono">{{ m.value }}</span>
          </div>
        </div>

        <div v-if="interruptNotice" class="intr-flag">
          <strong>搁置原因</strong>
          <span>{{ interruptNotice }}</span>
        </div>

        <div class="fn-row">
          <h4 class="fn-sec">运行日志</h4>
          <span v-if="isStreaming" class="live-tag">● 实时</span>
          <span v-else class="live-tag off">已结束</span>
        </div>
        <div ref="logEl" class="log" @scroll.passive="onScroll">
          <pre v-if="run.log?.length"><code v-for="(l, i) in run.log" :key="i">{{ l }}</code></pre>
          <div v-else class="muted">{{ isStreaming ? '等待输出…' : '暂无日志输出' }}</div>
        </div>
      </div>

      <div v-else class="d-body muted">{{ err || '加载中…' }}</div>
    </el-drawer>
  </teleport>
</template>

<style scoped>
.d-head { display: flex; align-items: center; }
.d-title { display: flex; align-items: center; gap: 10px; }
.d-name { font-size: 18px; font-weight: 700; }
.d-body { display: flex; flex-direction: column; gap: 18px; }
.fn-sec {
  margin: 0; font-size: 12px; color: var(--muted);
  font-weight: 600; text-transform: uppercase; letter-spacing: .5px;
}
.fn-row { display: flex; align-items: center; gap: 10px; }
.live-tag {
  font-size: 11px; color: #4ade80; background: rgba(74,222,128,.12);
  padding: 1px 8px; border-radius: 10px;
}
.live-tag.off { color: var(--muted); background: rgba(127,132,145,.12); }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 6px 20px; }
.m-item { display: flex; flex-direction: column; gap: 3px; padding: 4px 0; min-width: 0; }
.m-item label { font-size: 12px; color: var(--muted); }
.m-item span { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.intr-flag {
  display: flex; align-items: flex-start; gap: 10px;
  background: rgba(214, 74, 69, .10); border: 1px solid rgba(214, 74, 69, .35);
  color: var(--el-color-danger, #d64845); border-radius: 8px; padding: 10px 12px; font-size: 13px;
}
.intr-flag strong { white-space: nowrap; flex-shrink: 0; }
.intr-flag span { word-break: break-all; line-height: 1.5; }
.log { background: #0b0d10; font-family: var(--mono); font-size: 12px; max-height: 55vh; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.log pre { margin: 0; padding: 8px 10px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.log code { display: block; font-size: 12px; line-height: 1.55; }
</style>