<script setup>
import { onMounted, ref, watch, computed } from 'vue'
import { fetchRunning, abortRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel, dict } from '../constants'
import { refreshTick, bump } from '../store/refresh'
import { openRun } from '../store/drawer'
import { toast } from '../store/toast'
import AutoTablePanel from '../components/AutoTablePanel.vue'

const runs = ref([])
const total = ref(0)
const aliveTotal = ref(0)
const counts = ref({})
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)
const typeFilter = ref('')
const qFilter = ref('')
const statusFilter = ref('')

const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))
// Phase 2 · 执行中心状态筛选：按目标状态隔离（含异常状态 ORPHANED/UNKNOWN/STARTING）。
// 后端 /api/running 原生支持 status 过滤，分页总条数与筛选一致。
const statusOptions = [
  { v: '', l: '全部状态' },
  { v: 'RUNNING', l: '正常运行' },
  { v: 'STARTING', l: '预登记中' },
  { v: 'ORPHANED', l: '孤儿' },
  { v: 'UNKNOWN', l: '待核查' },
]
// 表格自适应高度 + 行高铺满统一由 AutoTablePanel 组件处理

// 排序：默认按开始时间倒序（新启动的在最前）
const sortField = ref('start')
const sortOrder = ref('desc')
function onSortChange({ prop, order }) {
  if (!prop || !order) return
  sortField.value = prop
  sortOrder.value = order === 'ascending' ? 'asc' : 'desc'
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  const params = { page: page.value, page_size: pageSize.value, sort: sortField.value, order: sortOrder.value }
  if (typeFilter.value) params.type = typeFilter.value
  if (qFilter.value.trim()) params.q = qFilter.value.trim()
  if (statusFilter.value) params.status = statusFilter.value
  try {
    const res = await fetchRunning(params)
    runs.value = res.items || []
    total.value = res.total || 0
    aliveTotal.value = res.alive_total ?? 0
    counts.value = res.counts || {}
  } finally {
    loading.value = false
  }
}

// Phase 2 · 执行中心分桶统计：正常/待处理(ORPHANED)/待核查(UNKNOWN+STARTING)
const stats = computed(() => {
  const c = counts.value || {}
  return {
    running: c.RUNNING || 0,
    orphaned: c.ORPHANED || 0,
    pending: (c.UNKNOWN || 0) + (c.STARTING || 0),
    aliveTotal: aliveTotal.value,
  }
})

// 条件变化自动刷新：下拉立即、名称 300ms 去抖
let qTimer = null
watch(typeFilter, () => { page.value = 1; load() })
watch(statusFilter, () => { page.value = 1; load() })
watch(qFilter, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page.value = 1; load() }, 300) })

onMounted(() => { load() })
// 其它页面发生动作（触发/中止）后，本页刷新一次
watch(refreshTick, () => load())

async function onAbort(run) {
  try {
    await abortRun(run.id)
    // 不在本地弹提示，后端中止成功的 finished 事件会经 SSE 弹出「已中止」
    bump() // 通知其他列表页刷新
    await load()
  } catch (e) {
    toast.error('中止失败: ' + (e?.response?.data?.detail || e.message))
  }
}
function open(run) { openRun(run.id) }

// Phase 2 · 状态角标样式归类（都走 constants 翻译，样式按状态分组）
const STATUS_CLASS = {
  RUNNING: 'RUNNING',
  STARTING: 'STARTING',
  ORPHANED: 'ORPHANED',
  UNKNOWN: 'UNKNOWN',
  INTERRUPTED: 'INTERRUPTED',
}
function statusClass(status) { return STATUS_CLASS[status] || 'NEUTRAL' }
</script>

<template>
  <div class="page">
    <!-- 搜索栏 -->
    <el-card shadow="never" class="toolbar">
      <el-form inline @submit.prevent>
        <el-form-item label="任务类型">
          <el-select v-model="typeFilter" placeholder="全部类型" clearable style="width:140px">
            <el-option v-for="o in typeOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="statusFilter" style="width:130px">
            <el-option v-for="o in statusOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务名称">
          <el-input v-model="qFilter" placeholder="搜索…" clearable style="width:200px" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Phase 2 · 分桶汇总：正常运行 / 待处理(ORPHANED) / 待核查(UNKNOWN+STARTING) -->
    <el-card shadow="never" class="statbar">
      <div class="stat">
        <span class="stat-num">{{ stats.running }}</span>
        <span class="stat-label">正常运行</span>
      </div>
      <div class="stat stat-orphan">
        <span class="stat-num">{{ stats.orphaned }}</span>
        <span class="stat-label">待处理</span>
      </div>
      <div class="stat stat-unknown">
        <span class="stat-num">{{ stats.pending }}</span>
        <span class="stat-label">待核查</span>
      </div>
      <div class="stat stat-alive">
        <span class="stat-num">{{ stats.aliveTotal }}</span>
        <span class="stat-label">实际运行进程数</span>
      </div>
    </el-card>

    <!-- 数据表：AutoTablePanel 统一处理高度/行高铺满/分页 -->
    <AutoTablePanel :data="runs" v-loading="loading" @sort-change="onSortChange"
                    :page="page" :total="total" :page-size="pageSize"
                    @change="p => { page = p; load() }"
                    :empty-text="runs.length ? '' : '当前没有正在执行的实例'">
        <el-table-column prop="name" label="任务名称" min-width="200" align="center">
          <template #default="{ row }"><strong>{{ row.job_name }}</strong></template>
        </el-table-column>
        <el-table-column label="任务类型" min-width="110" align="center">
          <template #default="{ row }"><span class="badge NEUTRAL">{{ jobTypeLabel(row.job_type) }}</span></template>
        </el-table-column>
        <el-table-column label="触发" min-width="90" align="center">
          <template #default="{ row }">{{ triggerLabel(row.trigger) }}</template>
        </el-table-column>
        <el-table-column label="PID" min-width="80" align="center">
          <template #default="{ row }"><span class="mono">{{ row.pid ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column prop="start" label="开始时间" min-width="150" sortable="custom" align="center">
          <template #default="{ row }">{{ (row.start || '').replace('T',' ').slice(5,19) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="120" align="center">
          <template #default="{ row }">
            <span class="badge" :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="150" align="center">
          <template #default="{ row }">
            <div class="ops">
              <el-button size="small" @click="open(row)">详情</el-button>
              <el-button size="small" type="danger" plain @click="onAbort(row)">中止</el-button>
            </div>
          </template>
        </el-table-column>
    </AutoTablePanel>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: var(--space); }
.toolbar :deep(.el-form-item) { margin-bottom: 0; }
.toolbar :deep(.el-form--inline) { flex-wrap: wrap; row-gap: 12px; }
.ops { display: flex; align-items: center; gap: 6px; }
.statbar { display: flex; gap: 24px; flex-wrap: wrap; }
.stat { display: flex; align-items: baseline; gap: 8px; }
.stat-num { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat-label { color: var(--el-text-color-secondary); font-size: 13px; }
.stat-orphan .stat-num { color: #D69A4B; }
.stat-unknown .stat-num { color: #909399; text-decoration: line-through; }
.stat-alive .stat-num { color: #6C9CFF; }
</style>