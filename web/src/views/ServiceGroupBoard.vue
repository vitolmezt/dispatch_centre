<script setup>
import { onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import {
  fetchServiceGroups, createServiceGroup, updateServiceGroup,
  deleteServiceGroup, startServiceGroup, stopServiceGroup,
  triggerJob, stopRun, fetchServiceGroup, fetchJobs,
} from '../api'
import { refreshTick } from '../store/refresh'
import { ignoreRunIds } from '../store/realtime'
import { toast } from '../store/toast'
import AutoTablePanel from '../components/AutoTablePanel.vue'
import RunLogPanel from '../components/RunLogPanel.vue'

const groups = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)
// 表格自适应高度 + 行高铺满统一由 AutoTablePanel 组件处理

async function load() {
  loading.value = true
  try {
    const res = await fetchServiceGroups()
    groups.value = res || []
    total.value = (res || []).length
  } finally {
    loading.value = false
  }
}

onMounted(() => { load() })
watch(refreshTick, () => load())

function allRunning(g) { return g.running }
function badgeType(g) { return g.running ? 'RUNNING' : 'NEUTRAL' }
function statusLabel(g) { return g.running ? '运行中' : '已停止' }

// ---- 新建/编辑 ----
const editorOpen = ref(false)
const editing = ref(null)
const jobOptions = ref([])  // 可选作成员的任务

async function openCreate() {
  editing.value = null
  jobOptions.value = await loadDetachJobs()
  editorOpen.value = true
}
async function openEdit(g) {
  editing.value = g
  jobOptions.value = await loadDetachJobs()
  editorOpen.value = true
}

async function loadDetachJobs() {
  try {
    const res = await fetchJobs({ all: true })
    const items = Array.isArray(res) ? res : (res?.items || [])
    // 候选成员：已存在于本组的任务仍可保留在列表
    return items
  } catch (e) { return [] }
}

function isDetach(j) {
  return !!j?.params?.detach
}

const draft = ref({ name: '', description: '', members: [] })

function buildDraft(g) {
  draft.value = {
    name: g?.name || '',
    description: g?.description || '',
    members: (g?.members || []).map(m => ({ job_name: m.job_name, label: m.label || m.job_name, order: m.order ?? 0 })),
  }
}

watch(editorOpen, (v) => { if (v) buildDraft(editing.value) })

async function onSave() {
  const members = draft.value.members
    .map(m => ({ job_name: m.job_name, label: m.label || m.job_name, order: Number(m.order) || 0 }))
    .filter(m => m.job_name)
  const payload = { name: draft.value.name.trim(), description: draft.value.description, members }
  if (!payload.name) { toast.error('请填写服务组名称'); return }
  if (members.length === 0) { toast.error('请至少选择一个成员任务'); return }
  try {
    if (editing.value) await updateServiceGroup(editing.value.name, payload)
    else await createServiceGroup(payload)
    toast.success(editing.value ? '服务组已更新' : '服务组已创建')
    editorOpen.value = false
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || e.message)
  }
}

function addMember() {
  const job = jobOptions.value.find(j => j.params?.detach && !draft.value.members.some(m => m.job_name === j.name))
  if (!job) { toast.info('没有更多可选的后台任务'); return }
  draft.value.members.push({ job_name: job.name, label: job.name, order: draft.value.members.length })
}
function removeMember(jn) {
  draft.value.members = draft.value.members.filter(m => m.job_name !== jn)
}

// ---- 启动 / 停止 ----
const acting = ref({})
async function onStart(g) {
  acting.value[g.name] = 'start'
  try {
    const res = await startServiceGroup(g.name)
    toast.success(`服务组「${g.name}」已启动`)
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || '启动失败')
  } finally {
    acting.value[g.name] = ''
  }
}
async function onStop(g) {
  acting.value[g.name] = 'stop'
  try {
    // 整组停止前，先申请忽略各运行成员 run 的"完成"通知，
    // 否则每个子服务(被强杀标 KILLED)都会单弹一条"已中止"，再由这里统一给一条汇总提示。
    ignoreRunIds((g?.members || []).map(m => m.run_id))
    await stopServiceGroup(g.name)
    toast.info(`服务组「${g.name}」已停止`)
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || '停止失败')
  } finally {
    acting.value[g.name] = ''
  }
}

async function onDelete(g) {
  try {
    await ElMessageBox.confirm(`确定删除服务组「${g.name}」？删除后不可恢复。`, '删除服务组', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch (e) {
    return // 用户取消
  }
  try {
    await deleteServiceGroup(g.name)
    toast.info(`服务组「${g.name}」已删除`)
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || '删除失败')
  }
}

// 「⋯」更多下拉的编辑/删除
function onMore(cmd, row) {
  if (cmd === 'edit') openEdit(row)
  else if (cmd === 'delete') onDelete(row)
}

// ---- 成员实时日志抽屉 ----
const logGroup = ref(null)
const logOpen = ref(false)

function openLog(g) {
  logGroup.value = g
  logOpen.value = true
}

function firstRunningMember(g) {
  return (g?.members || []).find(m => m.status === 'RUNNING') || null
}

// 单个成员独立启动：触发其底层 Job（常驻）
// 不再弹"正在启动/已启动"——是否成功由该面板标题栏的状态徽标实时反映，避免重复提示
async function startMember(member) {
  try {
    await triggerJob(member.job_name)
    await refreshGroup()
  } catch (e) {
    toast.error(e?.response?.data?.detail || `${member.label || member.job_name} 启动失败`)
  }
}
// 单个成员独立停止：按当前 run 的 PID 整树 kill
async function stopMember(member) {
  if (!member.run_id) { toast.info(`${member.label || member.job_name} 未在运行`); return }
  try {
    // 提前忽略该 run 的 finished 通知，避免 SSE 再弹一条"已中止"，与下面的"已停止"重复
    ignoreRunIds([member.run_id])
    await stopRun(member.run_id)
    toast.info(`${member.label || member.job_name} 已停止`)
    await refreshGroup()
  } catch (e) {
    toast.error(e?.response?.data?.detail || `${member.label || member.job_name} 停止失败`)
  }
}
async function refreshGroup() {
  if (!logGroup.value) return
  try {
    const fresh = await fetchServiceGroup(logGroup.value.name)
    if (fresh) {
      // 用守卫避免直接改 member 对象引用
      logGroup.value.members = fresh.members
    }
  } catch (e) { /* 忽略刷新错误，下次操作会重读 */ }
}
</script>

<template>
  <div class="page">
    <!-- 工具栏：标题 + 新建 -->
    <div class="toolbar-row">
      <div class="hint">把多个常驻（进程独立）任务编排为「一键启动/停止」的服务组，如前端 + 后端整套拉起。点某行「日志」可实时查看该组各成员终端输出。</div>
      <el-button type="primary" @click="openCreate">新建服务组</el-button>
    </div>

    <!-- 服务组表格 -->
    <AutoTablePanel :data="groups" v-loading="loading"
                    :page="page" :total="total" :page-size="pageSize"
                    @change="p => { page = p; load() }"
                    :empty-text="groups.length ? '' : '还没有服务组，点右上角新建。需先有「进程独立」的后台任务作为成员'">
        <el-table-column prop="name" label="服务组" min-width="160">
          <template #default="{ row }"><strong>{{ row.name }}</strong></template>
        </el-table-column>
        <el-table-column label="描述" min-width="140">
          <template #default="{ row }">
            <span class="muted-text">{{ row.description || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="成员" min-width="200">
          <template #default="{ row }">
            <div class="member-tags">
              <el-tag v-for="m in row.members" :key="m.job_name" size="small"
                      :type="m.status === 'RUNNING' ? 'success' : 'info'">
                {{ m.label }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="100" align="center">
          <template #default="{ row }">
            <span class="badge" :class="badgeType(row)">{{ statusLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="220" align="center">
          <template #default="{ row }">
            <div class="ops">
              <el-button size="small" @click="openLog(row)">日志</el-button>
              <el-button v-if="row.running" size="small" type="danger" plain
                         :loading="acting[row.name]==='stop'" @click="onStop(row)">停止</el-button>
              <el-button v-else size="small" type="primary"
                         :loading="acting[row.name]==='start'" @click="onStart(row)">启动</el-button>
              <el-dropdown trigger="click" @command="c => onMore(c, row)">
                <el-button size="small" text><span class="more-dots">⋯</span></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="edit">编辑</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
    </AutoTablePanel>

    <!-- 新建/编辑服务组抽屉 -->
    <el-drawer :model-value="editorOpen" :title="editing ? '编辑服务组' : '新建服务组'"
               size="520px" @close="editorOpen = false">
      <el-form label-width="72px" label-position="left">
        <el-form-item label="组名称">
          <el-input v-model="draft.name" placeholder="如：XX系统启动" :disabled="!!editing" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="draft.description" placeholder="这个服务组有什么用" />
        </el-form-item>
        <el-form-item label="成员">
          <div class="member-editor">
            <div v-for="(m, i) in draft.members" :key="m.job_name" class="member-row">
              <el-select v-model="m.job_name" style="flex:1" placeholder="选择后台任务">
                <el-option v-for="j in jobOptions" :key="j.name" :value="j.name"
                           :label="`${j.name}${isDetach(j) ? '（常驻）' : ''}`" />
              </el-select>
              <el-input v-model="m.label" style="flex:1" placeholder="别名（可选）" />
              <el-input-number v-model="m.order" :min="0" style="width:90px" controls-position="right" title="启动顺序" />
              <el-button text type="danger" @click="removeMember(m.job_name)" :disabled="draft.members.length<=1">删</el-button>
            </div>
            <el-button size="small" @click="addMember">+ 添加成员</el-button>
            <div class="tip">按 order 顺序依次启动；成员应为「进程独立」的常驻任务。</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" @click="onSave">保存</el-button>
      </template>
    </el-drawer>

    <!-- 成员实时日志抽屉：加宽占屏 ~3/5，成员左右横排，每个面板可独立启动/停止 -->
    <el-drawer :model-value="logOpen" :title="logGroup ? `实时日志 · ${logGroup.name}` : ''"
               size="60%" @close="logOpen = false">
      <div v-if="logGroup" class="log-view">
        <div class="log-view-hint">每个服务可单独「启动/停止」，改其中一个服务只需重启它，不必动整个组。</div>
        <RunLogPanel v-for="m in logGroup.members" :key="m.job_name"
                     :member="m" @start="startMember" @stop="stopMember" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: var(--space); height: 100%; min-height: 0; }
.toolbar-row {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
}
.hint { color: var(--muted); font-size: 12px; }
.muted-text { color: var(--muted); }
.ops {
  display: flex; justify-content: flex-end; align-items: center;
  gap: 8px; flex-wrap: nowrap; white-space: nowrap; width: 100%;
}
.ops :deep(.el-button) { margin: 0; }
.more-dots { font-size: 16px; line-height: 1; letter-spacing: 1px; padding: 0 2px; }
.member-tags { display: flex; gap: 4px; flex-wrap: wrap; }
.member-editor { width: 100%; display: flex; flex-direction: column; gap: 8px; }
.member-row { display: flex; gap: 6px; align-items: center; }
.tip { color: var(--muted); font-size: 12px; }
.log-view {
  display: flex; flex-wrap: wrap; align-items: stretch; gap: 14px;
  height: 100%; min-height: 0; align-content: flex-start;
  overflow-y: auto;
}
.log-view :deep(.run-log-panel) {
  flex: 0 0 calc(50% - 7px);   /* 固定两列：每个面板占一半宽（减 gap 的一半） */
  max-width: calc(50% - 7px);
}
.log-view :deep(.run-log-body) {
  max-height: 50vh; min-height: 200px;
}
.log-view-hint { width: 100%; color: var(--muted); font-size: 12px; flex-shrink: 0; }
.el-drawer__body { display: flex; flex-direction: column; }
.log-view { height: 100%; }
</style>