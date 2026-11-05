<script setup>
import { onMounted, ref, watch } from 'vue'
import { fetchRunning, abortRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel, dict } from '../constants'
import { refreshTick, bump } from '../store/refresh'
import { openRun } from '../store/drawer'
import { toast } from '../store/toast'
import PagerBar from '../components/PagerBar.vue'

const runs = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = 10
const typeFilter = ref('')
const qFilter = ref('')

const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))

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
  const params = { page: page.value, page_size: pageSize, sort: sortField.value, order: sortOrder.value }
  if (typeFilter.value) params.type = typeFilter.value
  if (qFilter.value.trim()) params.q = qFilter.value.trim()
  try {
    const res = await fetchRunning(params)
    runs.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}

// 条件变化自动刷新：下拉立即、名称 300ms 去抖
let qTimer = null
watch(typeFilter, () => { page.value = 1; load() })
watch(qFilter, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page.value = 1; load() }, 300) })

onMounted(load)
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
        <el-form-item label="任务名称">
          <el-input v-model="qFilter" placeholder="搜索…" clearable style="width:200px" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 数据表 -->
    <el-card shadow="never" class="table-card">
      <el-table :data="runs" v-loading="loading" @sort-change="onSortChange"
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
        <el-table-column label="状态" min-width="110" align="center">
          <template #default="{ row }"><span class="badge RUNNING">进行中</span></template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="150" align="center">
          <template #default="{ row }">
            <div class="ops">
              <el-button size="small" @click="open(row)">详情</el-button>
              <el-button size="small" type="danger" plain @click="onAbort(row)">中止</el-button>
            </div>
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
.ops { display: flex; align-items: center; gap: 6px; }
</style>