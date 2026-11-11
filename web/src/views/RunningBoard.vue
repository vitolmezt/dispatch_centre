<script setup>
import { onMounted, ref, watch } from 'vue'
import { fetchRunning, abortRun, fetchJobs } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel, dict } from '../constants'
import { refreshTick, bump } from '../store/refresh'
import { openRun, openJob } from '../store/drawer'
import { toast } from '../store/toast'
import AutoTablePanel from '../components/AutoTablePanel.vue'

const rows = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)
const typeFilter = ref('')
const qFilter = ref('')
const statusFilter = ref('')

const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))
const statusOptions = [
  { v: '', l: '全部状态' },
  { v: 'PENDING', l: '待开始' },
  { v: 'RUNNING', l: '正常运行' },
  { v: 'STARTING', l: '预登记中' },
  { v: 'ORPHANED', l: '孤儿' },
  { v: 'UNKNOWN', l: '待核查' },
]

const sortField = ref('start')
const sortOrder = ref('desc')
function onSortChange({ prop, order }) {
  if (!prop || !order) return
  sortField.value = prop
  sortOrder.value = order === 'ascending' ? 'asc' : 'desc'
  page.value = 1
  applyPage()
}

// 构建混合行：待开始的定时任务 + 正在执行的实例（定时任务在前）
function buildRows(sched, runs) {
  const schedRows = sched.map(j => ({
    kind: 'sched',
    name: j.name,
    job_name: j.name,
    job_type: j.type,
    trigger: 'cron',
    next_run_at: j.next_run_at || '',
    running_count: j.running_count || 0,
    status: 'PENDING',
  }))
  const runRows = runs.map(r => ({ ...r, kind: 'run' }))
  return [...schedRows, ...runRows]
}

async function load() {
  loading.value = true
  try {
    // 取回全部活跃实例 + 全部启用的定时任务，在前端合并并分页
    const [runRes, jobsRes] = await Promise.all([
      fetchRunning({ page: 1, page_size: 500 }),
      fetchJobs({ all: true }),
    ])
    const runs = runRes.items || []
    const items = Array.isArray(jobsRes) ? jobsRes : (jobsRes?.items || [])
    const sched = items.filter(j => j.schedule && j.enabled)
    rows.value = buildRows(sched, runs)
    total.value = rows.value.length
    applyPage()
  } catch (e) {
    rows.value = []
    total.value = 0
    pageRows.value = []
  } finally {
    loading.value = false
  }
}

const pageRows = ref([])
// 客户端过滤 + 排序 + 分页
function filteredRows() {
  let list = rows.value
  if (typeFilter.value) {
    const t = typeFilter.value.toLowerCase()
    list = list.filter(r => (r.job_type || '').toLowerCase() === t)
  }
  if (statusFilter.value) {
    list = list.filter(r => r.status === statusFilter.value)
  }
  if (qFilter.value.trim()) {
    const q = qFilter.value.trim().toLowerCase()
    list = list.filter(r => (r.job_name || r.name || '').toLowerCase().includes(q))
  }
  // 定时任务固定在前（PENDING），其余按开始时间排序
  const field = sortField.value === 'name' ? 'job_name' : 'start'
  const mul = sortOrder.value === 'asc' ? 1 : -1
  const sortable = list.filter(r => r.kind !== 'sched')
  sortable.sort((a, b) => ((a[field] || '') > (b[field] || '') ? 1 : -1) * mul)
  return [...list.filter(r => r.kind === 'sched'), ...sortable]
}
function applyPage() {
  total.value = filteredRows().length
  const start = (page.value - 1) * pageSize.value
  pageRows.value = filteredRows().slice(start, start + pageSize.value)
}
function onPage(p) { page.value = p; applyPage() }
function onSize(s) { pageSize.value = s; page.value = 1; applyPage() }

let qTimer = null
watch(typeFilter, () => { page.value = 1; applyPage() })
watch(statusFilter, () => { page.value = 1; applyPage() })
watch(qFilter, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page.value = 1; applyPage() }, 300) })

onMounted(() => { load() })

watch(refreshTick, () => load())

async function onAbort(run) {
  try {
    await abortRun(run.id)
    bump()
    await load()
  } catch (e) {
    toast.error('中止失败: ' + (e?.response?.data?.detail || e.message))
  }
}
function open(run) { openRun(run.id) }

// 整行点击：定时任务行打开任务详情，执行实例行打开执行详情（与任务列表/执行历史一致）
function onRowClick(row) {
  if (row.kind === 'run' && row.id) openRun(row.id)
  else if (row.job_name || row.name) openJob(row.job_name || row.name)
}

const STATUS_CLASS = {
  PENDING: 'PENDING',
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

    <AutoTablePanel :data="pageRows" v-loading="loading" @sort-change="onSortChange"
                    :page="page" :total="total" :page-size="pageSize"
                    @change="onPage" @size-change="onSize"
                    highlight-current-row clickable @row-click="onRowClick"
                    :empty-text="pageRows.length ? '' : '待开始的定时任务与执行中的实例都为空'">
        <el-table-column label="任务名称" min-width="180" align="center">
          <template #default="{ row }"><strong>{{ row.job_name || row.name }}</strong></template>
        </el-table-column>
        <el-table-column label="任务类型" min-width="100" align="center">
          <template #default="{ row }"><span class="badge NEUTRAL">{{ jobTypeLabel(row.job_type) }}</span></template>
        </el-table-column>
        <el-table-column label="触发" min-width="80" align="center">
          <template #default="{ row }">
            <span v-if="row.kind === 'sched'">定时</span>
            <span v-else>{{ triggerLabel(row.trigger) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="下次执行" min-width="160" align="center">
          <template #default="{ row }">
            <span v-if="row.kind === 'sched'" class="mono">{{ (row.next_run_at || '').replace('T', ' ').slice(0, 16) || '—' }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="PID" min-width="80" align="center">
          <template #default="{ row }"><span class="mono">{{ row.pid ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column prop="start" label="开始时间" min-width="140" sortable="custom" align="center">
          <template #default="{ row }">{{ (row.start || '').replace('T',' ').slice(5,19) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="100" align="center">
          <template #default="{ row }">
            <span class="badge" :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="150" align="center">
          <template #default="{ row }">
            <div v-if="row.kind === 'run'" class="ops" @click.stop>
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
</style>