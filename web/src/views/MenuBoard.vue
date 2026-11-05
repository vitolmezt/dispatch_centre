<script setup>
import { ref, inject, onMounted, watch } from 'vue'
import { jobTypeLabel, statusLabel, enabledLabel, dict } from '../constants'
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

function toggleSort(field) {
  if (sortField.value === field) {
    sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  } else {
    sortField.value = field
    sortOrder.value = 'desc'
  }
  page.value = 1
  load()
}

function sortIcon(field) {
  if (sortField.value !== field) return ''
  return sortOrder.value === 'desc' ? '▼' : '▲'
}
const fmtTime = (s) => (s || '').replace('T', ' ').slice(0, 16)

// 启动确认：记录当前待触发的任务名，气泡显示在其按钮正上方
const confirmRun = ref(null)
const running = ref(false)

// 动态表单单据（后端下发） + 新增/编辑弹窗状态
const schemas = ref({ types: {}, schemas: {} })
const modalOpen = ref(false)
const editingJob = ref(null) // null=新增, {..}=编辑

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

function reset() {
  typeFilter.value = ''
  enabledFilter.value = ''
  qFilter.value = ''
  page.value = 1
  load()
}

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
  const next = !job.enabled
  acting.value = { ...acting.value, [job.name]: true }
  try {
    await enableJob(job.name, next)
    toast.info(next ? `已启用「${job.name}」` : `已停用「${job.name}」`)
    load()
  } catch (e) {
    toast.error('操作失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    acting.value = { ...acting.value, [job.name]: false }
  }
}

async function onDelete(job) {
  if (!window.confirm(`删除任务「${job.name}」？`)) return
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
  <div>
    <!-- 搜索栏：左对齐，变化即刷新；重置按钮靠右 -->
    <div class="search">
      <label class="search-label">指令类型</label>
      <select v-model="typeFilter">
        <option value="">类型</option>
        <option v-for="o in typeOptions" :key="o.v" :value="o.v">{{ o.l }}</option>
      </select>
      <label class="search-label">启用状态</label>
      <select v-model="enabledFilter">
        <option value="">全部</option>
        <option v-for="o in enabledOptions" :key="o.v" :value="o.v">{{ o.l }}</option>
      </select>
      <label class="search-label">任务名称</label>
      <input v-model="qFilter" placeholder="搜索..." />
      <button class="reset" title="重置条件" @click="reset">重置</button>
      <button class="primary add-btn" @click="openCreate">+ 新增指令</button>
    </div>

    <div class="panel">
      <table class="mb">
        <colgroup>
          <col style="width:20%" />
          <col style="width:10%" />
          <col style="width:14%" />
          <col style="width:18%" />
          <col style="width:11%" />
          <col style="width:11%" />
          <col style="width:16%" />
        </colgroup>
        <thead>
          <tr>
            <th class="sortable" @click="toggleSort('name')">任务名 {{ sortIcon('name') }}</th>
            <th>类型</th>
            <th>描述</th>
            <th class="sortable" @click="toggleSort('created_at')">创建时间 {{ sortIcon('created_at') }}</th>
            <th>启用</th>
            <th>最近状态</th>
            <th class="col-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in items" :key="job.name" class="trow">
            <td><strong>{{ job.name }}</strong></td>
            <td><span class="badge NEUTRAL">{{ jobTypeLabel(job.type) }}</span></td>
            <td class="desc" :title="job.description || ''">
              <span class="desc-inner">{{ job.description || '—' }}</span>
            </td>
            <td class="mono">{{ fmtTime(job.created_at) || '—' }}</td>
            <td>
              <span :class="['toggle', job.enabled ? 'on' : 'off']">{{ enabledLabel(job.enabled) }}</span>
            </td>
            <td>
              <span v-if="job.last_status" :class="['badge', job.last_status]">{{ statusLabel(job.last_status) }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td class="ops">
              <!-- 启动按钮 + 顶部小弹窗 -->
              <div class="run-wrap">
                <button class="run-btn" :disabled="!job.enabled || acting[job.name]" @click.stop="confirmRun = job">▶ 启动</button>
                <div v-if="confirmRun === job" class="pop" @click.stop>
                  <div class="pop-msg">是否执行「{{ job.name }}」？</div>
                  <div class="pop-actions">
                    <button class="cancel" @click="confirmRun = null">取消</button>
                    <button class="ok" :disabled="running" @click="onConfirmRun">
                      {{ running ? '执行中…' : '确认执行' }}
                    </button>
                  </div>
                </div>
              </div>
              <button class="ops-btn" :disabled="acting[job.name]"
                      :title="job.enabled ? '停用' : '启用'" @click="onToggle(job)">
                {{ job.enabled ? '停用' : '启用' }}
              </button>
              <button class="ops-btn" :disabled="acting[job.name]" @click="openEdit(job)">编辑</button>
              <button class="ops-btn danger" :disabled="acting[job.name]" @click="onDelete(job)">删除</button>
              <button class="detail-btn" style="margin-left:4px" @click="openJob(job.name)">详情 ›</button>
            </td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="7" class="muted" style="text-align:center; padding:28px">
              {{ loading ? '加载中…' : '没有匹配的任务，换个搜索条件试试' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <PagerBar :page="page" :total="total" :page-size="pageSize" @change="p => { page = p; load() }" />

    <!-- 新增/编辑指令弹窗 -->
    <JobEditModal v-if="modalOpen" :title="editingJob ? '编辑指令' : '新增指令'"
                  :schemas="schemas" :initial="editingJob"
                  @close="onModalClose" @submit="onModalSubmit" />
  </div>
</template>

<style scoped>
.search {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 14px; /* 左对齐 */
}
.search-label { color: var(--muted); font-size: 13px; white-space: nowrap; }
.search select, .search input {
  background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
  border-radius: 7px; padding: 7px 10px; font-size: 13px;
}
.search input { width: 200px; }
.reset {
  margin-left: auto; /* 重置按钮推到最右 */
  background: transparent; border: 1px solid var(--border); color: var(--muted);
}
.reset:hover { color: var(--text); border-color: var(--text); }
.add-btn { margin-left: 8px; }
.desc { color: var(--muted); font-size: 13px; }
.trow:hover td { background: var(--panel-2); }
.toggle { padding: 2px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap; }
.toggle.on { background: #133423; color: var(--ok); }
.toggle.off { background: #332233; color: var(--warn); }

/* ---- 表格：固定列宽 + 内容居中 ---- */
.mb { table-layout: fixed; width: 100%; }
.mb th, .mb td {
  text-align: center;          /* 表头与表体内容居中 */
  vertical-align: middle;
  white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;   /* 防止内容溢出撑宽 */
}
/* 操作列：表头和按钮组都居中 → 表头正好在按钮组正中间 */
.mb th.col-ops { text-align: center; }
.mb td.ops { text-align: center; }
/* 操作列须允许子元素溢出，否则确认弹窗会被单元格裁掉 */
.mb td.ops { overflow: visible; }
/* 可点击排序的表头 */
.mb th.sortable { cursor: pointer; user-select: none; }
.mb th.sortable:hover { color: var(--primary); }
/* 描述：居中 + 单行省略，hover 看完整 */
.mb td.desc { width: auto; }
.mb td.desc .desc-inner {
  display: inline-block; max-width: 100%;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  vertical-align: middle;
}
/* 内部包裹标签强制同行、不换行 */
.mb td .badge, .mb td .toggle, .mb td strong {
  display: inline-flex; align-items: center; vertical-align: middle; max-width: 100%;
}

.ops { display: flex; justify-content: center; align-items: center; gap: 4px; white-space: nowrap; }
.run-wrap { position: relative; flex-shrink: 0; }
.ops-btn { padding: 5px 8px; font-size: 12px; }
.ops-btn.danger { color: var(--danger); border-color: #5a2a2c; }
/* 启动按钮正上方的小弹窗 */
.pop {
  position: absolute; bottom: calc(100% + 8px); right: 0; z-index: 30;
  background: var(--panel); border: 1px solid var(--border); border-radius: 9px;
  box-shadow: 0 8px 24px rgba(0,0,0,.5); padding: 12px 14px; width: 240px;
}
.pop::after {
  content: ''; position: absolute; top: 100%; right: 18px;
  border: 6px solid transparent; border-top-color: var(--border);
}
.pop-msg { font-size: 13px; margin-bottom: 10px; text-align: center; word-break: break-word; }
.pop-actions { display: flex; justify-content: center; gap: 6px; }
.pop-actions button { border-radius: 7px; padding: 6px 12px; font-size: 12px; cursor: pointer; }
.pop-actions .cancel { background: var(--panel-2); color: var(--text); border: 1px solid var(--border); }
.pop-actions .ok { background: var(--ok); color: #04120a; border: none; font-weight: 600; }
.pop-actions button:disabled { opacity: .5; }

button.run-btn { background: var(--ok); border: none; color: #04120a; font-weight: 600; }
button.run-btn:hover:not(:disabled) { filter: brightness(1.1); }
button.run-btn:disabled { opacity: .4; cursor: not-allowed; }
.detail-btn { background: transparent; border: 1px solid var(--primary); color: var(--primary); }
.detail-btn:hover { background: var(--primary); color: #fff; }
button { border-radius: 7px; padding: 7px 12px; font-size: 13px; cursor: pointer; }
</style>