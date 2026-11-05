<script setup>
import { onMounted, ref, watch, computed } from 'vue'
import { fetchHistory } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel, dict } from '../constants'
import { refreshTick } from '../store/refresh'
import { openRun } from '../store/drawer'
import PagerBar from '../components/PagerBar.vue'

const runs = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = 10

const statusFilter = ref('')
const typeFilter = ref('')
const qFilter = ref('')
let qTimer = null

const statusOptions = Object.entries(dict.groups?.STATUS || {}).map(([v, l]) => ({ v, l }))
const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))

// 排序：默认按执行开始时间倒序（最近执行的在前）
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
    const params = { page: page.value, page_size: pageSize, sort: sortField.value, order: sortOrder.value }
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
// 条件变化自动刷新：下拉立即、名称 300ms 去抖
watch([statusFilter, typeFilter], () => { page.value = 1; load() })
watch(qFilter, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page.value = 1; load() }, 300) })
onMounted(load)
// 其它页面发生动作（触发/中止）后，本页刷新一次
watch(refreshTick, () => load())

function open(run) { openRun(run.id) }
</script>

<template>
  <div class="page">
    <!-- 搜索栏 -->
    <el-card shadow="never" class="toolbar">
      <el-form inline @submit.prevent>
        <el-form-item label="指令类型">
          <el-select v-model="typeFilter" placeholder="全部" clearable style="width:130px">
            <el-option v-for="o in typeOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行状态">
          <el-select v-model="statusFilter" placeholder="全部" clearable style="width:130px">
            <el-option v-for="o in statusOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="指令名称">
          <el-input v-model="qFilter" placeholder="搜索…" clearable style="width:190px" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 数据表 -->
    <el-card shadow="never" class="table-card">
      <el-table :data="runs" v-loading="loading" @sort-change="onSortChange"
                :empty-text="loading ? '加载中…' : '暂无记录，去「指令列表」页触发一个任务试试'"
                highlight-current-row @row-click="open" class="clickable">
        <el-table-column prop="id" label="Run ID" min-width="90" />
        <el-table-column prop="name" label="任务" min-width="150" sortable="custom">
          <template #default="{ row }"><strong>{{ row.job_name }}</strong></template>
        </el-table-column>
        <el-table-column label="类型" min-width="90">
          <template #default="{ row }"><span class="badge NEUTRAL">{{ jobTypeLabel(row.job_type) }}</span></template>
        </el-table-column>
        <el-table-column label="触发" min-width="80">
          <template #default="{ row }">{{ triggerLabel(row.trigger) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="100">
          <template #default="{ row }">
            <span :class="['badge', row.status]"
                  :title="statusLabel(row.status) + (row.error ? ' · ' + row.error : '')">
              {{ statusLabel(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="start" label="执行开始时间" min-width="140" sortable="custom">
          <template #default="{ row }">{{ fmtT(row.start) }}</template>
        </el-table-column>
        <el-table-column label="执行结束时间" min-width="140">
          <template #default="{ row }">{{ fmtT(row.end) || '—' }}</template>
        </el-table-column>
        <el-table-column label="耗时" min-width="80">
          <template #default="{ row }">{{ row.duration_ms != null ? (row.duration_ms/1000).toFixed(2)+'s' : '—' }}</template>
        </el-table-column>
        <el-table-column label="退出码" min-width="70">
          <template #default="{ row }">
            <span v-if="row.status === 'FAILED' || row.status === 'KILLED' || row.status === 'TIMEDOUT'">—</span>
            <span v-else>{{ row.exit_code ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="90">
          <template #default="{ row }">
            <el-button size="small" @click.stop="open(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <PagerBar :page="page" :total="total" :page-size="pageSize" @change="p => { page = p; load() }" />
    </el-card>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: var(--space); }
.toolbar :deep(.el-form-item) { margin-bottom: 0; }
.toolbar :deep(.el-form--inline) { flex-wrap: wrap; row-gap: 12px; }
.clickable { cursor: pointer; }
</style>