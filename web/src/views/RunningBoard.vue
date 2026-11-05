<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchRunning, abortRun } from '../api'
import { statusLabel, jobTypeLabel, triggerLabel, dict } from '../constants'
import { refreshTick, bump } from '../store/refresh'
import PagerBar from '../components/PagerBar.vue'

const router = useRouter()
const runs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const typeFilter = ref('')
const qFilter = ref('')

const typeOptions = Object.entries(dict.groups?.JOB_TYPE || {}).map(([v, l]) => ({ v, l }))

// 排序：默认按开始时间倒序（新启动的在最前）
const sortField = ref('start')
const sortOrder = ref('desc')
function toggleSort(field) {
  if (sortField.value === field) sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  else { sortField.value = field; sortOrder.value = 'desc' }
  page.value = 1
  load()
}
function sortIcon(field) {
  if (sortField.value !== field) return ''
  return sortOrder.value === 'desc' ? '▼' : '▲'
}

async function load() {
  const params = { page: page.value, page_size: pageSize, sort: sortField.value, order: sortOrder.value }
  if (typeFilter.value) params.type = typeFilter.value
  if (qFilter.value.trim()) params.q = qFilter.value.trim()
  const res = await fetchRunning(params)
  runs.value = res.items || []
  total.value = res.total || 0
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
    alert('中止失败: ' + (e?.response?.data?.detail || e.message))
  }
}
function open(run) { router.push(`/run/${run.id}`) }

function reset() {
  typeFilter.value = ''
  qFilter.value = ''
  page.value = 1
  load()
}
</script>

<template>
  <div>
    <div class="search">
      <label class="search-label">指令类型</label>
      <select v-model="typeFilter">
        <option value="">全部类型</option>
        <option v-for="o in typeOptions" :key="o.v" :value="o.v">{{ o.l }}</option>
      </select>
      <label class="search-label">任务名称</label>
      <input v-model="qFilter" placeholder="搜索…" />
      <button class="reset" title="重置条件" @click="reset">重置</button>
    </div>

    <div class="panel">
      <table class="tb">
        <colgroup>
          <col style="width:16%" />
          <col />
          <col style="width:10%" />
          <col style="width:8%" />
          <col style="width:8%" />
          <col style="width:13%" />
          <col style="width:10%" />
          <col style="width:13%" />
        </colgroup>
        <thead>
          <tr>
            <th>Run ID</th>
            <th class="sortable" @click="toggleSort('name')">任务 {{ sortIcon('name') }}</th>
            <th>类型</th>
            <th>触发</th>
            <th>PID</th>
            <th class="sortable" @click="toggleSort('start')">开始时间 {{ sortIcon('start') }}</th>
            <th>状态</th>
            <th class="col-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.id">
            <td class="mono">{{ run.id }}</td>
            <td class="jname"><strong>{{ run.job_name }}</strong></td>
            <td><span class="badge NEUTRAL">{{ jobTypeLabel(run.job_type) }}</span></td>
            <td>{{ triggerLabel(run.trigger) }}</td>
            <td class="mono">{{ run.pid ?? '—' }}</td>
            <td class="mono">{{ (run.start || '').replace('T',' ').slice(5,19) }}</td>
            <td><span class="badge RUNNING">进行中</span></td>
            <td class="ops">
              <button @click="open(run)">详情</button>
              <button class="danger" style="margin-left:6px" @click="onAbort(run)">中止</button>
            </td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="8" class="muted" style="text-align:center; padding:24px">当前没有正在执行的实例</td>
          </tr>
        </tbody>
      </table>
    </div>

    <PagerBar :page="page" :total="total" :page-size="pageSize" @change="p => { page = p; load() }" />
  </div>
</template>

<style scoped>
.search {
  display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
}
.search-label { color: var(--muted); font-size: 13px; white-space: nowrap; }
.search select, .search input {
  background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
  border-radius: 7px; padding: 7px 10px; font-size: 13px;
}
.search input { width: 190px; }
.reset {
  margin-left: auto; background: transparent; border: 1px solid var(--border); color: var(--muted);
}
.reset:hover { color: var(--text); border-color: var(--text); }
.col-c { white-space: nowrap; text-align: center; }
/* 固定列宽布局：确保每行始终为横向表格，绝不堆叠 */
.tb { table-layout: fixed; }
.tb th, .tb td {
  text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  vertical-align: middle;
}
/* 操作列：表头和按钮组都居中 */
.tb th.col-ops { text-align: center; }
.tb td.ops { text-align: center; }
.tb th.sortable { cursor: pointer; user-select: none; }
.tb th.sortable:hover { color: var(--primary); }
.tb td.jname { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tb td .badge { display: inline-flex; align-items: center; vertical-align: middle; }
.ops { display: flex; justify-content: center; align-items: center; gap: 6px; white-space: nowrap; }
button { border-radius: 7px; padding: 7px 12px; font-size: 13px; cursor: pointer; }
</style>