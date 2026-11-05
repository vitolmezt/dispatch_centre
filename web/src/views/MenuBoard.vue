<script setup>
import { ref, inject, onMounted, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { jobTypeLabel, enabledLabel, dict } from '../constants'
import { fetchJobs, triggerJob, createJob, updateJob, deleteJob, enableJob, fetchJobSchema } from '../api'
import { openJob } from '../store/drawer'
import { refreshTick, bump } from '../store/refresh'
import { toast } from '../store/toast'
import PagerBar from '../components/PagerBar.vue'
import JobEditModal from '../components/JobEditModal.vue'

const reloadJobs = inject('reloadJobs', async () => {})

const items = ref([])
const total = ref(0)
const loading = ref(true)
const page = ref(1)
const pageSize = 10

// 搜索条件（下拉/输入变化即自动刷新）
const typeFilter = ref('')
const enabledFilter = ref('')
const qFilter = ref('')

// 排序：默认按创建时间倒序（新任务在前）
const sortField = ref('created_at')
const sortOrder = ref('desc')

function onSortChange({ prop, order }) {
  if (!prop || !order) return
  sortField.value = prop
  sortOrder.value = order === 'ascending' ? 'asc' : 'desc'
  page.value = 1
  load()
}
const fmtTime = (s) => (s || '').replace('T', ' ').slice(0, 16)

// 启动确认：记录当前待触发的任务名，气泡显示在其按钮正上方
const confirmRun = ref(null)
const running = ref(false)

// 动态表单单据（后端下发） + 新增/编辑弹窗状态
const schemas = ref({ types: {}, schemas: {} })
const modalOpen = ref(false)
const editingJob = ref(null) // null=新增, {..}=编辑
// 只读查看弹窗状态：点击表格行弹出「任务详情」（样式同编辑表单，仅查看不可编辑）
const viewOpen = ref(false)
const viewJob = ref(null)

// 操作中按钮的禁用态：jobName -> bool
const acting = ref({})

const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))
const enabledOptions = Object.entries(dict.groups?.ENABLED || {}).map(([v, l]) => ({ v, l }))

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize, sort: sortField.value, order: sortOrder.value }
    if (typeFilter.value) params.type = typeFilter.value
    if (enabledFilter.value) params.enabled = enabledFilter.value
    if (qFilter.value.trim()) params.q = qFilter.value.trim()
    const res = await fetchJobs(params)
    items.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}

// 条件变化自动刷新（名称模糊去抖）
watch([typeFilter, enabledFilter], () => { page.value = 1; load() })
let qTimer = null
watch(qFilter, () => {
  clearTimeout(qTimer)
  qTimer = setTimeout(() => { page.value = 1; load() }, 300)
})

async function loadSchema() {
  try {
    const d = await fetchJobSchema()
    schemas.value = { types: d?.types || {}, schemas: d?.schemas || {} }
  } catch (e) { /* ignore */ }
}

async function onConfirmRun() {
  if (!confirmRun.value || running.value) return
  running.value = true
  try {
    await triggerJob(confirmRun.value.name)
    toast.success(`已启动「${confirmRun.value.name}」`)
    await reloadJobs()
    bump() // 通知其他列表页刷新
  } catch (e) {
    const code = e?.response?.status
    const detail = e?.response?.data?.detail || e.message
    if (code === 409) toast.info(detail)   // 正在运行，阻止
    else if (code === 403) toast.error(detail)   // 已停用
    else toast.error(detail)
  } finally {
    running.value = false
    confirmRun.value = null
    load()
  }
}

// ---- 新增 / 编辑 ----
function openCreate() {
  editingJob.value = null
  modalOpen.value = true
}

function openEdit(job) {
  editingJob.value = JSON.parse(JSON.stringify(job))
  modalOpen.value = true
}

// 点击表格行 → 只读详情（样式同编辑表单，不可编辑）
function openView(job) {
  viewJob.value = JSON.parse(JSON.stringify(job))
  viewOpen.value = true
}

async function onModalSubmit(payload) {
  try {
    if (editingJob.value) {
      await updateJob(editingJob.value.name, payload)
      toast.success(`已更新「${payload.name}」`)
    } else {
      await createJob(payload)
      toast.success(`已新增「${payload.name}」`)
    }
    modalOpen.value = false
    editingJob.value = null
    await reloadJobs()
    load()
  } catch (e) {
    toast.error('保存失败: ' + (e?.response?.data?.detail || e.message))
  }
}

function onModalClose() {
  modalOpen.value = false
  editingJob.value = null
}

// ---- 停用/启用 与 删除 ----
async function onToggle(job) {
  if (job.enabled && job.running_count > 0) {
    toast.info(`「${job.name}」正在执行中，不能停用`)
    return
  }
  const next = !job.enabled
  acting.value = { ...acting.value, [job.name]: true }
  try {
    await enableJob(job.name, next)
    toast.info(next ? `已启用「${job.name}」` : `已停用「${job.name}」`)
    load()
  } catch (e) {
    const status = e?.response?.status
    const detail = e?.response?.data?.detail || e.message
    toast.error(status === 409 ? detail : '操作失败: ' + detail)
  } finally {
    acting.value = { ...acting.value, [job.name]: false }
  }
}

async function onDelete(job) {
  try {
    await ElMessageBox.confirm(`确定删除任务「${job.name}」？删除后不可恢复。`, '删除任务', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch (e) {
    return // 用户取消
  }
  acting.value = { ...acting.value, [job.name]: true }
  try {
    await deleteJob(job.name)
    toast.info(`已删除「${job.name}」`)
    await reloadJobs()
    load()
  } catch (e) {
    toast.error('删除失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    acting.value = { ...acting.value, [job.name]: false }
  }
}

onMounted(() => { load(); loadSchema() })
// 其它页面发生动作（触发/中止）后，本页刷新一次
watch(refreshTick, () => load())
</script>

<template>
  <div class="page">
    <!-- 搜索栏：表格上方统一工具栏 -->
    <el-card shadow="never" class="toolbar">
      <el-form inline @submit.prevent>
        <el-form-item label="任务类型">
          <el-select v-model="typeFilter" placeholder="类型" clearable style="width:130px">
            <el-option v-for="o in typeOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用状态">
          <el-select v-model="enabledFilter" placeholder="全部" clearable style="width:120px">
            <el-option v-for="o in enabledOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务名称">
          <el-input v-model="qFilter" placeholder="搜索..." clearable style="width:200px" />
        </el-form-item>
        <el-form-item class="toolbar-btn">
          <el-button type="primary" @click="openCreate">+ 新增任务</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 数据表 -->
    <el-card shadow="never" class="table-card">
      <el-table :data="items" v-loading="loading" stripe
                @sort-change="onSortChange" @row-click="openView"
                :empty-text="loading ? '加载中…' : '没有匹配的任务，换个搜索条件试试'">
        <el-table-column prop="name" label="任务名称" min-width="180" align="center">
          <template #default="{ row }"><strong class="tname">{{ row.name }}</strong></template>
        </el-table-column>
        <el-table-column label="任务类型" min-width="110" align="center">
          <template #default="{ row }"><span class="badge NEUTRAL">{{ jobTypeLabel(row.type) }}</span></template>
        </el-table-column>
        <el-table-column label="任务描述" min-width="160" show-overflow-tooltip align="center">
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="启用" min-width="90" align="center">
          <template #default="{ row }">
            <span :class="['toggle', row.enabled ? 'on' : 'off']">{{ enabledLabel(row.enabled) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="当前状态" min-width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.running_count > 0" class="badge RUNNING">进行中</span>
            <span v-else class="badge IDLE">空闲</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="150" sortable="custom" align="center">
          <template #default="{ row }">{{ fmtTime(row.created_at) || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="290" align="center">
          <template #default="{ row }">
            <div class="ops" @click.stop>
              <!-- 启动按钮 + 确认弹窗 -->
              <el-popover :visible="confirmRun === row" placement="top" :width="240">
                <template #reference>
                  <el-button size="small" :disabled="!row.enabled || acting[row.name]"
                             class="run-btn" @click="confirmRun = row">▶ 启动</el-button>
                </template>
                <div class="pop-inner">
                  <div class="pop-msg">是否执行「{{ row.name }}」？</div>
                  <div class="pop-actions">
                    <el-button size="small" @click="confirmRun = null">取消</el-button>
                    <el-button size="small" type="success" :loading="running" @click="onConfirmRun">
                      {{ running ? '执行中…' : '确认执行' }}
                    </el-button>
                  </div>
                </div>
              </el-popover>
              <el-button size="small" :disabled="acting[row.name] || (row.enabled && row.running_count > 0)"
                         :title="(row.enabled && row.running_count > 0) ? '正在执行中，不能停用' : ''"
                         @click="onToggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
              <el-button size="small" :disabled="acting[row.name]" @click="openEdit(row)">编辑</el-button>
              <el-button size="small" type="danger" plain :disabled="acting[row.name]" @click="onDelete(row)">删除</el-button>
              <el-button size="small" text type="primary" @click="openJob(row.name)">详情 ›</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <PagerBar :page="page" :total="total" :page-size="pageSize" @change="p => { page = p; load() }" />
    </el-card>

    <!-- 新增/编辑任务弹窗 -->
    <JobEditModal v-if="modalOpen" :title="editingJob ? '编辑任务' : '新增任务'"
                  :schemas="schemas" :initial="editingJob"
                  @close="onModalClose" @submit="onModalSubmit" />

    <!-- 只读详情弹窗：点击表格行弹出，样式同编辑表单，仅查看不可编辑 -->
    <JobEditModal v-if="viewOpen" title="任务详情" readonly
                  :schemas="schemas" :initial="viewJob"
                  @close="viewOpen = false" />
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: var(--space); }
.toolbar { overflow-x: auto; }
/* 搜索栏：整行单选，筛选条件与按钮处同一水平行；屏幕不足时横向滚动而非换行 */
.toolbar :deep(.el-form--inline) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: var(--space);
}
.toolbar :deep(.el-form-item) { margin-bottom: 0; margin-right: 0; flex: 0 0 auto; }
/* 「+ 新增任务」顶到最右端 */
.toolbar :deep(.el-form-item.toolbar-btn) { margin-left: auto; }

.tname { font-weight: 600; }
.toggle { padding: 2px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap; }
.toggle.on { background: #133423; color: var(--ok); }
.toggle.off { background: #332233; color: var(--warn); }

.ops { display: flex; align-items: center; gap: 6px; flex-wrap: nowrap; }
.run-btn { font-weight: 600; }
.pop-inner { text-align: center; }
.pop-msg { margin-bottom: 10px; font-size: 13px; word-break: break-word; }
.pop-actions { display: flex; justify-content: center; gap: 6px; }
</style>