<script setup>
import { onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { fetchHistory, deleteHistory } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel, dict } from '../constants'
import { refreshTick } from '../store/refresh'
import { openRun } from '../store/drawer'
import { toast } from '../store/toast'
import AutoTablePanel from '../components/AutoTablePanel.vue'

const runs = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)
const selected = ref([])
const deleting = ref(false)
const statusFilter = ref('')
const typeFilter = ref('')
const qFilter = ref('')
let qTimer = null
const statusOptions = Object.entries(dict.groups?.STATUS || {}).map(([v, l]) => ({ v, l }))
const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))
const sortField = ref('start')
const sortOrder = ref('desc')
function onSortChange({ prop, order }) {
  if (!prop || !order) return
  sortField.value = prop
  sortOrder.value = order === 'ascending' ? 'asc' : 'desc'
  page.value = 1
  load()
}

function fmtT(s) { return (s || '').replace('T', ' ').slice(5, 19) }

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, sort: sortField.value, order: sortOrder.value }
    if (statusFilter.value) params.status = statusFilter.value
    if (typeFilter.value) params.type = typeFilter.value
    if (qFilter.value.trim()) params.q = qFilter.value.trim()
    const res = await fetchHistory(params)
    runs.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}
watch([statusFilter, typeFilter], () => { page.value = 1; load() })
watch(qFilter, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page.value = 1; load() }, 300) })
onMounted(() => { load() })
watch(refreshTick, () => load())

function open(run) { openRun(run.id) }
function onRowClick(row, column) {
  if (column.type === 'selection') return
  open(row)
}

function onSelectionChange(rows) { selected.value = rows }

async function onBatchDelete() {
  const ids = selected.value.map(r => r.id)
  if (!ids.length) return
  const count = ids.length
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${count} 条执行记录？删除后不可恢复。`, '批量删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch (e) {
    return
  }
  deleting.value = true
  try {
    const res = await deleteHistory(ids)
    toast.info(`已删除 ${res.deleted ?? count} 条执行记录`)
    selected.value = []
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || '删除失败')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="toolbar">
      <el-form inline @submit.prevent>
        <el-form-item label="任务类型">
          <el-select v-model="typeFilter" placeholder="全部" clearable style="width:130px">
            <el-option v-for="o in typeOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行状态">
          <el-select v-model="statusFilter" placeholder="全部" clearable style="width:130px">
            <el-option v-for="o in statusOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务名称">
          <el-input v-model="qFilter" placeholder="搜索…" clearable style="width:190px" />
        </el-form-item>
        <el-form-item>
          <el-button type="danger" plain :disabled="!selected.length" :loading="deleting"
                     @click="onBatchDelete">批量删除</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <AutoTablePanel :data="runs" v-loading="loading" @sort-change="onSortChange"
                    :page="page" :total="total" :page-size="pageSize"
                    @change="p => { page = p; load() }"
                    row-key="id" @selection-change="onSelectionChange"
                    highlight-current-row clickable @row-click="onRowClick"
                    :empty-text="loading ? '加载中…' : '暂无记录，去「任务列表」页触发一个任务试试'">
        <el-table-column type="selection" width="44" align="center" />
        <el-table-column prop="name" label="任务名称" min-width="150" sortable="custom" align="center">
          <template #default="{ row }"><strong>{{ row.job_name }}</strong></template>
        </el-table-column>
        <el-table-column label="任务类型" min-width="90" align="center">
          <template #default="{ row }"><span class="badge NEUTRAL">{{ jobTypeLabel(row.job_type) }}</span></template>
        </el-table-column>
        <el-table-column label="触发" min-width="80" align="center">
          <template #default="{ row }">{{ triggerLabel(row.trigger) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="100" align="center">
          <template #default="{ row }">
            <span :class="['badge', row.status]"
                  :title="statusLabel(row.status) + (row.error ? ' · ' + row.error : '')">
              {{ statusLabel(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="start" label="执行开始时间" min-width="140" sortable="custom" align="center">
          <template #default="{ row }">{{ fmtT(row.start) }}</template>
        </el-table-column>
        <el-table-column prop="end" label="执行结束时间" min-width="140" sortable="custom" align="center">
          <template #default="{ row }">{{ fmtT(row.end) || '—' }}</template>
        </el-table-column>
        <el-table-column label="耗时" min-width="80" align="center">
          <template #default="{ row }">{{ row.duration_ms != null ? (row.duration_ms/1000).toFixed(2)+'s' : '—' }}</template>
        </el-table-column>
        <el-table-column label="退出码" min-width="70" align="center">
          <template #default="{ row }">
            <span v-if="row.status === 'FAILED' || row.status === 'KILLED' || row.status === 'TIMEDOUT'">—</span>
            <span v-else>{{ row.exit_code ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="90" align="center">
          <template #default="{ row }">
            <el-button size="small" @click.stop="open(row)">详情</el-button>
          </template>
        </el-table-column>
    </AutoTablePanel>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: var(--space); }
.toolbar :deep(.el-form-item) { margin-bottom: 0; }
.toolbar :deep(.el-form--inline) { flex-wrap: wrap; row-gap: 12px; }
</style>