<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { fetchRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel } from '../constants'
import { refreshTick } from '../store/refresh'

/**
 * 全局右侧抽屉：展示一条执行记录的详情。
 *  - 基本信息（类型 / 触发 / PID / 开始 / 结束 / 耗时 / 退出码 / 错误）
 *  - 运行日志（实时）：运行中每 2.5s 轮询一次 + 每次全局事件立即刷新
 */

const props = defineProps({
  id: String,                 // run id
})
const emit = defineEmits(['close'])

const open = ref(false)
const run = ref(null)
const err = ref('')
let timer = null

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

async function load(runId = props.id) {
  err.value = ''
  if (!runId) return
  try {
    run.value = await fetchRun(runId)
  } catch (e) {
    err.value = '加载失败: ' + (e?.message || e)
  }
  refreshTimer()
}

// 运行期间定时轮询，实时抓取终端输出
function refreshTimer() {
  clearInterval(timer)
  if (open.value && run.value?.status === 'RUNNING') {
    timer = setInterval(async () => {
      try { run.value = await fetchRun(props.id) } catch (e) { /* keep last */ }
    }, 2500)
  }
}

// 其它页面全局事件后也刷新一次
watch(refreshTick, () => { if (open.value) load() })

onBeforeUnmount(() => { if (timer) clearInterval(timer) })
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

        <h4 class="fn-sec">运行日志</h4>
        <div class="log">
          <pre v-if="run.log?.length"><code v-for="(l, i) in run.log" :key="i">{{ l }}</code></pre>
          <div v-else class="muted">暂无日志输出</div>
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
.meta { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 4px 20px; }
.m-item { display: flex; flex-direction: column; gap: 4px; padding: 4px 0; }
.m-item label { font-size: 12px; color: var(--muted); }
.m-item span { font-size: 13px; word-break: break-all; }
.log { background: #0b0d10; font-family: var(--mono); font-size: 12px; max-height: 55vh; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.log pre { margin: 0; padding: 8px 10px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.log code { display: block; font-size: 12px; line-height: 1.55; }
</style>