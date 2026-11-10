<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { streamRunLog, fetchRun } from '../api'

const props = defineProps({
  member: Object,
})
const emit = defineEmits(['start', 'stop'])

const lines = ref([])
const ended = ref(false)
const endStatus = ref('')
const endError = ref('')
const body = ref(null)
let stream = null

function scrollToBottom() {
  nextTick(() => { if (body.value) body.value.scrollTop = body.value.scrollHeight })
}

async function subscribe() {
  lines.value = []
  ended.value = false
  endStatus.value = ''
  endError.value = ''
  stream?.close?.()
  stream = null
  if (!props.member?.run_id) return
  const rid = props.member.run_id
  stream = streamRunLog(rid, {
    onLine: (l) => { lines.value.push(l); scrollToBottom() },
    onEnd: async () => {
      ended.value = true
      try {
        const d = await fetchRun(rid)
        endStatus.value = d?.status || endStatus.value
        endError.value = d?.error || ''
      } catch (e) {
        endStatus.value = endStatus.value || 'FINISHED'
      }
      scrollToBottom()
    },
  })
}

watch(() => props.member?.run_id, subscribe)
onMounted(subscribe)
onUnmounted(() => stream?.close?.())

const busy = ref(false)
async function onStart() { busy.value = true; try { await emit('start', props.member) } finally { busy.value = false } }
async function onStop() { busy.value = true; try { await emit('stop', props.member) } finally { busy.value = false } }

const STATE_LABELS = {
  SUCCEEDED: '已完成', KILLED: '已中止', TIMEDOUT: '已超时',
  FAILED: '执行失败', RUNNING: '运行中', PENDING: '待启动',
  STOPPED: '已停止',
}
function statusText() {
  const s = ended.value ? (endStatus.value || 'FINISHED') : props.member?.status
  const base = STATE_LABELS[s] || s || '未知'
  if (!ended.value) return base
  const m = /^退出码 (\d+)$/.exec(`${endError.value || ''}`.trim())
  if (s === 'FAILED' && m && m[1] !== '0') return `${base}（非正常退出）`
  return base
}
</script>

<template>
  <div v-if="member" class="run-log-panel">
    <div class="run-log-head">
      <span class="run-log-name">{{ member.label || member.job_name }}</span>
      <span class="run-log-status"
            :class="{ is_run: member.status === 'RUNNING', is_end: ended }">{{ ended ? statusText() : member.status }}</span>
      <span v-if="member.pid" class="run-log-pid">PID {{ member.pid }}</span>
      <div class="run-log-ops" @click.stop>
        <el-button size="small" type="primary" plain :disabled="member.status==='RUNNING'" :loading="busy" @click="onStart">启动</el-button>
        <el-button size="small" type="danger" plain :disabled="member.status!=='RUNNING'" :loading="busy" @click="onStop">停止</el-button>
      </div>
    </div>
    <div class="run-log-body" ref="body">
      <template v-if="member.run_id">
        <div v-if="ended" class="run-log-end">
          <span class="run-log-end-badge">{{ statusText() }}</span>
          <span v-if="endError" class="run-log-end-err">{{ endError }}</span>
          <span v-else-if="!lines.length" class="run-log-empty-inline">（无输出）</span>
        </div>
        <div v-if="lines.length" class="run-log-lines">
          <div v-for="(l, i) in lines" :key="i" class="run-log-line">{{ l }}</div>
        </div>
        <div v-else-if="!ended" class="run-log-empty">等待输出…</div>
      </template>
      <div v-else class="run-log-empty">未在运行</div>
    </div>
  </div>
</template>

<style scoped>
.run-log-panel {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg);
  display: flex; flex-direction: column;
  min-height: 0;
}
.run-log-head {
  flex-shrink: 0;
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; border-bottom: 1px solid var(--border);
  background: var(--panel-2);
  font-size: 13px;
}
.run-log-name { font-weight: 600; color: var(--text); }
.run-log-status {
  font-size: 11px; padding: 1px 8px; border-radius: 10px;
  background: var(--bg-panel-2); color: var(--text-secondary);
}
.run-log-status.is_run { background: rgba(53,196,109,.18); color: var(--ok); }
.run-log-status.is_end { background: rgba(240,88,88,.18); color: var(--danger); }
.run-log-pid { margin-left: auto; color: var(--muted); font-family: Consolas, monospace; font-size: 12px; }
.run-log-ops { display: flex; gap: 6px; margin-left: 12px; }
.run-log-body {
  height: 37vh; overflow: auto; overscroll-behavior: contain;
  padding: 8px 12px; font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 1.5;
  box-sizing: border-box;
}
.run-log-lines { display: flex; flex-direction: column; }
.run-log-line { color: var(--text); white-space: pre-wrap; word-break: break-all; }
.run-log-empty { color: var(--muted); }
.run-log-end {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  margin-bottom: 8px; font-size: 12px;
}
.run-log-end-badge {
  flex-shrink: 0; padding: 1px 8px; border-radius: 10px; font-weight: 600;
  background: rgba(240,88,88,.18); color: var(--danger);
}
.run-log-end-err { color: var(--danger); word-break: break-all; }
.run-log-empty-inline { color: var(--muted); }
.run-log-empty { color: var(--muted); }
</style>