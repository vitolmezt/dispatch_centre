<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { jobTypeLabel, statusLabel } from '../constants'
import { fetchHistory } from '../api'

/**
 * 全局右侧抽屉（约半屏）：展示一个菜单项的
 *  1. 基础信息（name / 描述 / 类型 / enabled）
 *  2. 执行内容（params 按类型驱动展示）
 *  3. 配置信息（schedule / timeout / enabled）
 *  4. 执行经历（该任务最近几次历史）
 * 纵向从上到下表格布局，仿管理系统风格。
 * 触发按钮在「指令列表」表格内已有，这里不再放执行按钮。
 */

const props = defineProps({
  id: String,                 // 当前选中任务名
  jobs: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

const open = computed(() => !!props.id)
const job = computed(() => props.jobs.find(j => j.name === props.id) || null)

const recentRuns = ref([])

function fmtParam(v, f) {
  if (v == null || v === '') return '—'
  if (f?.type === 'bool') return v ? '是' : '否'
  if (Array.isArray(v)) return v.length ? v.join(' ') : '—'
  return String(v)
}

async function loadHistory() {
  if (!job.value) return
  recentRuns.value = []
  try {
    const data = await fetchHistory({ job: job.value.name, limit: 5, all: true })
    recentRuns.value = Array.isArray(data) ? data : (data?.runs || data?.items || [])
  } catch (e) {
    recentRuns.value = []
  }
}

// 打开抽屉时加载该任务最近执行记录
onMounted(() => { if (open.value) loadHistory() })
watch(() => props.id, (v) => { if (v) loadHistory() })
</script>

<template>
  <teleport to="body">
    <el-drawer :model-value="open" size="660px" :show-close="true"
               :with-header="true" append-to-body @close="emit('close')">
      <template #header>
        <div class="d-head">
          <div class="d-title">
            <span class="d-name">{{ job?.name }}</span>
            <span class="badge NEUTRAL">{{ jobTypeLabel(job?.type) }}</span>
          </div>
        </div>
      </template>

      <div v-if="job" class="d-body">
        <!-- 1 执行区 -->
        <section>
          <h4 class="fn-sec">执行</h4>
          <div class="kv">
            <div class="kv-row">
              <label>类型</label>
              <span class="mono">{{ jobTypeLabel(job.type) }}</span>
            </div>
            <div class="kv-row">
              <label>描述</label>
              <span>{{ job.description || '—' }}</span>
            </div>
          </div>
        </section>

        <!-- 2 执行内容（类型驱动 params） -->
        <section>
          <h4 class="fn-sec">执行内容</h4>
          <div class="kv">
            <template v-for="f in (job.params_schema?.fields || [])" :key="f.key">
              <div class="kv-row">
                <label>{{ f.label }}</label>
                <span class="mono">{{ fmtParam(job.params?.[f.key], f) }}</span>
              </div>
            </template>
            <div v-if="!(job.params_schema?.fields?.length)" class="kv-row">
              <label>参数</label>
              <span class="mono">{{ job.params ? JSON.stringify(job.params) : '—' }}</span>
            </div>
          </div>
        </section>

        <!-- 3 配置信息 -->
        <section>
          <h4 class="fn-sec">配置信息</h4>
          <div class="kv">
            <div class="kv-row"><label>定时</label><span class="mono">{{ job.schedule || '手动' }}</span></div>
            <div class="kv-row"><label>超时</label><span class="mono">{{ job.timeout_s ? job.timeout_s + ' 秒' : '不限' }}</span></div>
          </div>
        </section>

        <!-- 4 执行经历 -->
        <section>
          <h4 class="fn-sec">执行经历</h4>
          <div v-if="recentRuns.length" class="runs">
            <div v-for="r in recentRuns" :key="r.id" class="run-row">
              <span :class="['badge', r.status]">{{ statusLabel(r.status) }}</span>
              <span class="muted mono">{{ r.start ? r.start.slice(5, 19) : '—' }}</span>
              <span class="mono">{{ r.duration_ms != null ? (r.duration_ms / 1000).toFixed(2) + 's' : '—' }}</span>
            </div>
          </div>
          <div v-else class="muted missing">暂无执行记录</div>
        </section>
      </div>

      <div v-else class="d-body muted">未找到任务</div>
    </el-drawer>
  </teleport>
</template>

<style scoped>
.d-head { display: flex; align-items: center; }
.d-title { display: flex; align-items: center; gap: 10px; }
.d-name { font-size: 18px; font-weight: 700; }
.d-body { display: flex; flex-direction: column; gap: 18px; }
section { display: flex; flex-direction: column; gap: 8px; }
.fn-sec {
  margin: 0; font-size: 12px; color: var(--muted);
  font-weight: 600; text-transform: uppercase; letter-spacing: .5px;
}
.kv { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.kv-row { display: flex; border-bottom: 1px solid var(--border); }
.kv-row:last-child { border-bottom: none; }
.kv-row label {
  width: 110px; flex: none; padding: 9px 12px;
  color: var(--muted); font-size: 13px; background: var(--panel-2);
}
.kv-row span { padding: 9px 12px; font-size: 13px; word-break: break-all; }
.runs { display: flex; flex-direction: column; gap: 6px; }
.run-row {
  display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; font-size: 13px;
}
.missing { text-align: center; padding: 14px; }
</style>