<script setup>
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue'
import { fetchRun, streamRunLog } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel } from '../constants'
import { refreshTick } from '../store/refresh'

/**
 * 全局右侧抽屉：展示一条执行记录的详情。
 *  - 基本信息（类型 / 触发 / PID / 开始 / 结束 / 耗时 / 退出码 / 错误）
 *  - 运行日志：运行中经 SSE 实时流式追加（不轮询），结束后展示完整历史。
 *    run.log 在内存/落库均为有界(最近2000行)——超出覆盖最旧，避免无限膨胀。
 */

const props = defineProps({
  id: String,                 // run id
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
  return items
})

// 切换是否跟随滚动到底
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

// 运行中：后续日志走 SSE 实时流（替代旧的 2.5s 轮询）
function startStream(runId) {
  if (isStreaming || !run.value || run.value.status !== 'RUNNING') return
  isStreaming = true
  // 回放 fetchRun 已带回的 log（避免重复拉流首段），新行即时追加
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

// 其它页面全局事件后也刷新一次（列表状态变化）；运行中日志仍走流，仅刷新元数据
watch(refreshTick, async () => {
  if (!open.value || !run.value) return
  const cached = run.value
  try {
    const fresh = await fetchRun(props.id)
    // 保留实时流已追加的日志行，仅用新元数据刷新基本信息
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
        <!-- 元信息网格 -->
        <div class="meta">
          <template v-for="m in metaItems" :key="m.label">
            <div class="m-item"><label>{{ m.label }}</label><span class="mono">{{ m.value }}</span></div>
          </template>
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
.meta { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 4px 20px; }
.m-item { display: flex; flex-direction: column; gap: 4px; padding: 4px 0; }
.m-item label { font-size: 12px; color: var(--muted); }
.m-item span { font-size: 13px; word-break: break-all; }
.log { background: #0b0d10; font-family: var(--mono); font-size: 12px; max-height: 55vh; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.log pre { margin: 0; padding: 8px 10px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.log code { display: block; font-size: 12px; line-height: 1.55; }
</style>