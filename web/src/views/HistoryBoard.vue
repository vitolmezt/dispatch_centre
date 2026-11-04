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

function fmtT(s) { return (s || '').replace('T', ' ').slice(5, 19) }

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
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

function reset() {
  statusFilter.value = ''
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
        <option value="">全部</option>
        <option v-for="o in typeOptions" :key="o.v" :value="o.v">{{ o.l }}</option>
      </select>
      <label class="search-label">执行状态</label>
      <select v-model="statusFilter">
        <option value="">全部</option>
        <option v-for="o in statusOptions" :key="o.v" :value="o.v">{{ o.l }}</option>
      </select>
      <label class="search-label">指令名称</label>
      <input v-model="qFilter" placeholder="搜索…" />
      <button class="reset" title="重置条件" @click="reset">重置</button>
    </div>

    <div class="panel">
      <table class="tb">
        <colgroup>
          <col style="width:16%" />
          <col style="width:13%" />
          <col style="width:10%" />
          <col style="width:8%" />
          <col style="width:10%" />
          <col style="width:11%" />
          <col style="width:11%" />
          <col style="width:8%" />
          <col style="width:7%" />
          <col style="width:11%" />
        </colgroup>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>任务</th>
            <th>类型</th>
            <th>触发</th>
            <th>状态</th>
            <th>执行开始时间</th>
            <th>执行结束时间</th>
            <th>耗时</th>
            <th>退出码</th>
            <th class="col-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.id" class="clickable" @click="open(run)">
            <td class="mono">{{ run.id }}</td>
            <td class="jname"><strong>{{ run.job_name }}</strong></td>
            <td><span class="badge NEUTRAL">{{ jobTypeLabel(run.job_type) }}</span></td>
            <td>{{ triggerLabel(run.trigger) }}</td>
            <td class="stcol">
              <span :class="['badge', run.status]"
                    :title="run.error ? (statusLabel(run.status) + (run.error ? ' · ' + run.error : '')) : statusLabel(run.status)">
                {{ statusLabel(run.status) }}
              </span>
            </td>
            <td class="mono">{{ fmtT(run.start) }}</td>
            <td class="mono">{{ fmtT(run.end) || '—' }}</td>
            <td class="mono">{{ run.duration_ms != null ? (run.duration_ms/1000).toFixed(2)+'s' : '—' }}</td>
            <td class="mono">
              <!-- 失败/异常时不展示退出码 -->
              <span v-if="run.status === 'FAILED' || run.status === 'KILLED' || run.status === 'TIMEDOUT'">—</span>
              <span v-else>{{ run.exit_code ?? '—' }}</span>
            </td>
            <td class="ops">
              <button @click.stop="open(run)">详情</button>
            </td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="10" class="muted" style="text-align:center; padding:24px">
              {{ loading ? '加载中…' : '暂无记录，去「指令列表」页触发一个任务试试' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <PagerBar :page="page" :total="total" :page-size="pageSize" @change="p => { page = p; load() }" />
  </div>
</template>

<style scoped>
.clickable { cursor: pointer; }
.clickable:hover td { background: var(--panel-2); }
/* 固定列宽布局：列宽百分比严格固定，单元格绝不换行、绝不堆叠 */
.tb { table-layout: fixed; }
.tb th, .tb td {
  text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  vertical-align: middle;
}
/* 操作列：表头和按钮组都居中 */
.tb th.col-ops { text-align: center; }
.tb td.ops { text-align: center; }
.tb .stcol { vertical-align: middle; }
.tb .stcol .badge, .tb td .badge { display: inline-flex; align-items: center; vertical-align: middle; }
.ops { display: flex; justify-content: center; align-items: center; gap: 6px; white-space: nowrap; }
button { border-radius: 7px; padding: 7px 12px; font-size: 13px; cursor: pointer; }
.search {
  display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
  width: 100%; box-sizing: border-box;
}
.search-label { color: var(--muted); font-size: 13px; white-space: nowrap; }
.search select, .search input {
  background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
  border-radius: 7px; padding: 7px 10px; font-size: 13px;
}
.search input { width: 190px; }
.reset {
  margin-left: auto; /* 重置按钮推到搜索框右侧 */
  background: transparent; border: 1px solid var(--border); color: var(--muted);
}
.reset:hover { color: var(--text); border-color: var(--text); }
</style>