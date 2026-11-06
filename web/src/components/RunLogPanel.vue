<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { streamRunLog, fetchRun } from '../api'

/**
 * 服务组成员实时日志面板。
 * 成员正在运行时，通过 SSE /api/runs/{id}/stream 订阅该 run 的实时输出并滚动展示。
 * run 结束（含秒失败）时收到 end 哨兵，自动把面板切到结束状态，并拉最新 run 详情
 * 显示真实状态与人类可读的错误原因。
 * 每个面板可独立启动/停止该成员，便于只重启某个服务而不用动整个组。
 */
const props = defineProps({
  member: Object,
})
const emit = defineEmits(['start', 'stop'])

const lines = ref([])
const ended = ref(false)          // 该 run 是否已收到结束哨兵
const endStatus = ref('')         // 结束时从最新 run 详情读到的真实状态（FAILED/KILLED…）
const endError = ref('')          // 结束时从最新 run 详情读到的错误原因（人类可读）
const body = ref(null)
let stream = null

function scrollToBottom() {
  // DOM 已更新后，把滚动条钉到最底部，跟随最新输出
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
      // run 已结束：拉最新详情拿到真实状态与错误原因（失败/中止…），避免停留快照的 RUNNING
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

// 成员 run_id 变化（重启/停止）时重新订阅
watch(() => props.member?.run_id, subscribe)
onMounted(subscribe)
onUnmounted(() => stream?.close?.())

const busy = ref(false)
async function onStart() { busy.value = true; try { await emit('start', props.member) } finally { busy.value = false } }
async function onStop() { busy.value = true; try { await emit('stop', props.member) } finally { busy.value = false } }

// 状态翻译：把英文状态/退出码转成人话
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
  background: #0c0e12;
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
  background: #2b2f38; color: var(--muted);
}
.run-log-status.is_run { background: rgba(53,196,109,.18); color: var(--ok); }
.run-log-status.is_end { background: rgba(240,88,88,.18); color: #f07070; }
.run-log-pid { margin-left: auto; color: var(--muted); font-family: Consolas, monospace; font-size: 12px; }
.run-log-ops { display: flex; gap: 6px; margin-left: 12px; }
.run-log-body {
  /* 固定高度 + 内部滚动：日志超长内部滚动、不再撑开面板 */
  height: 37vh; overflow: auto; overscroll-behavior: contain;
  padding: 8px 12px; font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 1.5;
  box-sizing: border-box;
}
.run-log-lines { display: flex; flex-direction: column; }
.run-log-line { color: #c9d1d9; white-space: pre-wrap; word-break: break-all; }
.run-log-empty { color: var(--muted); }
.run-log-end {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  margin-bottom: 8px; font-size: 12px;
}
.run-log-end-badge {
  flex-shrink: 0; padding: 1px 8px; border-radius: 10px; font-weight: 600;
  background: rgba(240,88,88,.18); color: #f07070;
}
.run-log-end-err { color: #f0a0a0; word-break: break-all; }
.run-log-empty-inline { color: var(--muted); }
.run-log-empty { color: var(--muted); }
</style>