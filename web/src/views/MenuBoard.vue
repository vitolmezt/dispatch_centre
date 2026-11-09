<script setup>
import { ref, inject, onMounted, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { jobTypeLabel, enabledLabel, dict } from '../constants'
import { fetchJobs, triggerJob, createJob, updateJob, deleteJob, enableJob, fetchJobSchema } from '../api'
import { openJob } from '../store/drawer'
import { refreshTick, bump } from '../store/refresh'
import { toast } from '../store/toast'
import AutoTablePanel from '../components/AutoTablePanel.vue'
import JobEditModal from '../components/JobEditModal.vue'

const reloadJobs = inject('reloadJobs', async () => {})

const items = ref([])
const total = ref(0)
const loading = ref(true)
const page = ref(1)
const pageSize = ref(10)
// 表格自适应高度 + 行高铺满统一由 AutoTablePanel 组件处理（传 fill=10 即可）

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
    const params = { page: page.value, page_size: pageSize.value, sort: sortField.value, order: sortOrder.value }
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

// 筛选重置：清空全部筛选并回到第一页
function resetFilters() {
  typeFilter.value = ''
  enabledFilter.value = ''
  qFilter.value = ''
  page.value = 1
  load()
}

// 「更多操作」下拉命令分发
function onCommand(cmd, job) {
  if (cmd === 'edit') openEdit(job)
  else if (cmd === 'toggle') onToggle(job)
  else if (cmd === 'detail') openJob(job.name)
  else if (cmd === 'delete') onDelete(job)
}

onMounted(() => {
  load(); loadSchema()
})
// 其它页面发生动作（触发/中止）后，本页刷新一次
watch(refreshTick, () => load())
</script>

<template>
  <div class="job-page">
    <!-- 任务筛选工具栏 -->
    <el-card shadow="never" class="toolbar">
      <el-form inline class="toolbar-form" @submit.prevent>
        <div class="toolbar-group">
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
            <el-input v-model="qFilter" placeholder="搜索任务名称…" clearable style="width:220px" class="q-input" />
          </el-form-item>
        </div>
        <div class="toolbar-group toolbar-action">
          <el-button @click="resetFilters">重置</el-button>
          <el-button type="primary" @click="openCreate">新增任务</el-button>
        </div>
      </el-form>
    </el-card>

    <!-- 任务列表主体：AutoTablePanel 统一处理高度/行高铺满/分页 -->
    <AutoTablePanel :data="items" :loading="loading" stripe fill="10"
                    :page="page" :total="total" :page-size="pageSize"
                    @change="p => { page = p; load() }"
                    @sort-change="onSortChange" @row-click="openView"
                    :empty-text="loading ? '加载中…' : '没有匹配的任务，换个搜索条件试试'"
                    class="job-table">
        <el-table-column prop="name" label="任务名称" min-width="150" show-overflow-tooltip>
          <template #default="{ row }"><strong class="tname">{{ row.name }}</strong></template>
        </el-table-column>
        <el-table-column label="任务类型" min-width="100">
          <template #default="{ row }"><span class="badge NEUTRAL">{{ jobTypeLabel(row.type) }}</span></template>
        </el-table-column>
        <el-table-column label="任务描述" min-width="220" show-overflow-tooltip>
          <template #default="{ row }"><span class="tdesc">{{ row.description || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="启用" min-width="70">
          <template #default="{ row }">
            <span :class="['toggle', row.enabled ? 'on' : 'off']">{{ enabledLabel(row.enabled) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="当前状态" min-width="80">
          <template #default="{ row }">
            <span v-if="row.running_count > 0" class="badge RUNNING">进行中</span>
            <span v-else class="badge IDLE">空闲</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="140" sortable="custom">
          <template #default="{ row }"><span class="mono">{{ fmtTime(row.created_at) || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="240" align="center">
          <template #default="{ row }">
            <div class="ops" @click.stop>
              <!-- 主操作：启动（执行一次，含确认气泡） -->
              <el-popover :visible="confirmRun === row" placement="top" :width="240">
                <template #reference>
                  <el-button size="small"
                             :disabled="!row.enabled || (row.enabled && row.running_count > 0) || acting[row.name]"
                             :title="(row.enabled && row.running_count > 0) ? '该任务正在执行中' : (!row.enabled ? '任务已停用' : '')"
                             class="run-btn" @click="confirmRun = row">▶ 执行</el-button>
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

              <!-- 启用/停用：互斥行内主按钮（已启用→停用，已停用→启用） -->
              <el-button v-if="row.enabled" size="small" type="danger" plain :loading="acting[row.name]===row.name"
                         :disabled="row.running_count > 0" :title="row.running_count > 0 ? '任务正在执行中，不能停用' : ''"
                         @click="onToggle(row)">停用</el-button>
              <el-button v-else size="small" type="primary"
                         :loading="acting[row.name]===row.name" @click="onToggle(row)">启用</el-button>

              <!-- 更多操作下拉 -->
              <el-dropdown trigger="click" @command="c => onCommand(c, row)" @click.stop>
                <el-button size="small" text><span class="more-dots">⋯</span></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="edit" :disabled="acting[row.name]">编辑</el-dropdown-item>
                    <el-dropdown-item command="detail" :disabled="acting[row.name]">详情</el-dropdown-item>
                    <el-dropdown-item divided command="delete" :disabled="acting[row.name]"
                                      class="danger-item">删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
    </AutoTablePanel>

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
/* ===== 页布局：标题/工具栏固定，表格占满剩余高度可滚动，分页固定底部 ===== */
.job-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space);
  overflow: hidden;   /* 页内滚动交给表格主体，本层不滚 */
}

.toolbar {
  flex-shrink: 0;
  overflow: hidden;   /* 预留横向，窄屏时由 toolbar-form 处理 */
  border-radius: var(--radius-sm);
  --el-card-padding: 10px 14px;
}
.toolbar-form {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space);
  flex-wrap: wrap;   /* 窄屏允许换行，不挤压变形 */
}
.toolbar-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 18px;
}
.toolbar-action { flex-wrap: nowrap; gap: 8px; }
.toolbar :deep(.el-form-item) {
  margin-bottom: 0;
  margin-right: 0;
}
.toolbar :deep(.el-form-item__label) { color: var(--muted); font-size: 12px; margin-right: 6px; }

.tname { font-weight: 600; color: var(--text); font-size: 13px; }
.tdesc { color: var(--text-secondary, #ccd2dc); font-size: 12px; }

/* 启停小标签：简洁文字标签 */
.toggle { padding: 2px 9px; border-radius: 4px; font-size: 12px; white-space: nowrap; line-height: 1.7; }
.toggle.on { background: #14301f; color: #3fbf82; }
.toggle.off { background: #2c2417; color: var(--warn); }

/* ---- 操作列 ---- */
.ops { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: nowrap; white-space: nowrap; width: 100%; }
.ops :deep(.el-button) { margin: 0; }
.more-dots { font-size: 16px; line-height: 1; letter-spacing: 1px; padding: 0 2px; }
.run-btn { font-weight: 600; }
.pop-inner { text-align: center; }
.pop-msg { margin-bottom: 10px; font-size: 13px; word-break: break-word; }
.pop-actions { display: flex; justify-content: center; gap: 6px; }

/* 更多下拉：停用项用警示色、删除项用危险色 */
:deep(.el-dropdown-menu__item.is-warn) { color: var(--warn); }
:deep(.el-dropdown-menu__item.danger-item) { color: var(--danger); }
:deep(.el-dropdown-menu__item.danger-item:hover) { background: #3a2023; }

/* ---- 底部分页固定 ---- */
.table-footer {
  flex-shrink: 0;
  border-top: 1px solid var(--border);
  background: var(--panel);
}

/* ---- 响应式：窄屏时筛选换行、搜索框占宽 ---- */
@media (max-width: 900px) {
  .toolbar-form { flex-direction: column; align-items: stretch; }
  .toolbar-group { justify-content: flex-start; }
  .q-input { flex: 1; }
}
@media (max-width: 560px) {
  .job-table :deep(.el-table__row) { height: 42px; }
}
</style>