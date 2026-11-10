<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { fetchHistory } from '../api'
import { refreshTick } from '../store/refresh'


const loading = ref(false)
const unloadMsg = ref('')

const stats = ref({
  total: 0, running: 0, timedout: 0, killed: 0, success: 0, failed: 0, avgMs: 0,
  statusMap: {}, jobCount: {}, jobStatus: {}, dayOk: {}, dayAll: {},
})

function aggregate(runs) {
  const s = { total: 0, running: 0, timedout: 0, killed: 0, success: 0, failed: 0, avgMs: 0, sumMs: 0, nDur: 0 }
  const statusMap = {}
  const jobCount = {}
  const jobStatus = {}
  const dayOk = {}, dayAll = {}

  for (const r of runs) {
    s.total++
    const st = (r.status || 'UNKNOWN').toUpperCase()
    statusMap[st] = (statusMap[st] || 0) + 1
    if (st === 'SUCCEEDED') s.success++
    else if (st === 'FAILED') s.failed++
    else if (st === 'TIMEDOUT') s.timedout++
    else if (st === 'KILLED') s.killed++
    else if (st === 'RUNNING') s.running++

    jobCount[r.job_name] = (jobCount[r.job_name] || 0) + 1
    jobStatus[r.job_name] = jobStatus[r.job_name] || { ok: 0, fail: 0 }
    if (st === 'SUCCEEDED') jobStatus[r.job_name].ok++
    else if (st !== 'RUNNING') jobStatus[r.job_name].fail++

    if (r.duration_ms != null) { s.sumMs += r.duration_ms; s.nDur++ }
    if (r.start) {
      const d = r.start.slice(0, 10)
      dayAll[d] = (dayAll[d] || 0) + 1
      if (st === 'SUCCEEDED') dayOk[d] = (dayOk[d] || 0) + 1
    }
  }
  if (s.nDur) s.avgMs = Math.round(s.sumMs / s.nDur)
  s.statusMap = statusMap
  s.jobCount = jobCount
  s.jobStatus = jobStatus
  s.dayOk = dayOk
  s.dayAll = dayAll
  return s
}

function periodDelta(getter) {
  const runs = stats.value.runsFull || []
  if (runs.length < 4) return null
  const sorted = [...runs].sort((a, b) => (a.start > b.start ? 1 : a.start < b.start ? -1 : 0))
  const half = Math.floor(sorted.length / 2)
  const first = sorted.slice(0, half)
  const last = sorted.slice(half)
  const a = getter(last), b = getter(first)
  if (b === 0) return null
  const d = ((a - b) / b) * 100
  return isFinite(d) ? (d >= 0 ? '+' : '') + d.toFixed(1) + '%' : null
}

function periodRateDelta() {
  const runs = stats.value.runsFull || []
  if (runs.length < 4) return null
  const sorted = [...runs].sort((a, b) => (a.start > b.start ? 1 : a.start < b.start ? -1 : 0))
  const half = Math.floor(sorted.length / 2)
  const a = sorted.slice(half), b = sorted.slice(0, half)
  const rate = arr => arr.length ? arr.filter(x => x.status === 'SUCCEEDED').length / arr.length * 100 : 0
  const d = rate(a) - rate(b)
  return isFinite(d) ? (d >= 0 ? '+' : '') + d.toFixed(1) + 'pp' : null
}

const kpis = computed(() => {
  const s = stats.value
  const total = s.total
  const succRate = total ? (s.success / total * 100).toFixed(1) : '0'
  return [
    { label: '总执行次数', value: total.toLocaleString(), delta: periodDelta(r => r.length) || '—', note: '较上一周期', tone: 'blue', icon: '↗' },
    { label: '任务成功率', value: succRate + '%', delta: periodRateDelta() || '—', note: '较上一周期', tone: 'green', icon: '✓' },
    { label: '正在运行', value: String(s.running).padStart(2, '0'), delta: '实时', note: '当前活跃任务', tone: 'violet', icon: '◉' },
    { label: '失败 / 超时', value: String(s.failed + s.timedout + s.killed), delta: '—', note: '较上一周期', tone: 'red', icon: '!' },
  ]
})


const STATUS_META = {
  SUCCEEDED: { name: '已完成', color: '#43C99A' },
  KILLED: { name: '已中止', color: '#D69A4B' },
  TIMEDOUT: { name: '已超时', color: '#E7B84B' },
  FAILED: { name: '失败', color: '#EF6A70' },
  RUNNING: { name: '进行中', color: '#6C9CFF' },
}
const statusItems = computed(() => {
  const m = stats.value.statusMap || {}
  const total = stats.value.total || 1
  return Object.keys(STATUS_META)
    .map(k => ({
      key: k, name: STATUS_META[k].name, color: STATUS_META[k].color,
      count: m[k] || 0, percent: total ? Number(((m[k] || 0) / total * 100).toFixed(1)) : 0,
    }))
    .filter(i => i.count > 0)
})

const topJobs = computed(() =>
  Object.entries(stats.value.jobCount || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([name, runs]) => ({ name, runs }))
)
const healthJobs = computed(() =>
  Object.entries(stats.value.jobStatus || {})
    .filter(([, v]) => v.ok + v.fail > 0)
    .map(([name, v]) => {
      const t = v.ok + v.fail
      return { name, rate: Math.round(v.ok / t * 100), totalJob: t }
    })
    .sort((a, b) => b.rate - a.rate)
    .slice(0, 5)
)

const ranges = [
  { label: '近 24 小时', days: 1 },
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
]
const activeRange = ref('近 7 天')

function trendSeries() {
  const ok = stats.value.dayOk || {}
  const all = stats.value.dayAll || {}
  const days = ranges.find(r => r.label === activeRange.value)?.days || 7
  let dates = Object.keys(all).sort()
  if (days > 0) dates = dates.slice(-days)
  return {
    labels: dates.map(d => d.slice(5).replace('-', '/')),
    success: dates.map(d => ok[d] || 0),
    failed: dates.map(d => (all[d] || 0) - (ok[d] || 0)),
  }
}

const trendEl = ref(null)
const statusEl = ref(null)
const rankEl = ref(null)
const healthEl = ref(null)
let charts = []

const commonText = '#9AA5B5'
const gridLine = 'rgba(148, 163, 184, .10)'

function makeCharts() {
  charts.forEach(c => c?.dispose())
  charts = [trendEl.value, statusEl.value, rankEl.value, healthEl.value].map(el => el ? echarts.init(el) : null)

  const trend = trendSeries()
  charts[0]?.setOption({
    animationDuration: 700,
    tooltip: { trigger: 'axis', backgroundColor: '#202631', borderColor: '#343D4C', textStyle: { color: '#E8EDF5' } },
    legend: { top: 0, right: 0, icon: 'circle', itemWidth: 9, itemHeight: 9, textStyle: { color: commonText, fontSize: 11 }, data: [{ name: '成功', itemStyle: { color: '#55CDA2' } }, { name: '失败', itemStyle: { color: '#F0747A' } }] },
    grid: { left: 8, right: 12, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: trend.labels, axisLabel: { color: commonText, fontSize: 11 }, axisLine: { lineStyle: { color: gridLine } }, axisTick: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: commonText, fontSize: 11 }, splitLine: { lineStyle: { color: gridLine, type: 'dashed' } }, axisLine: { show: false } },
    series: [
      { name: '成功', type: 'line', smooth: .38, symbol: 'none', data: trend.success, lineStyle: { width: 2.5, color: '#55CDA2' }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(85,205,162,.20)' }, { offset: 1, color: 'rgba(85,205,162,0)' }]) } },
      { name: '失败', type: 'line', smooth: .38, symbol: 'none', data: trend.failed, lineStyle: { width: 2, color: '#F0747A' } },
    ],
  })

  const items = statusItems.value
  charts[1]?.setOption({
    animationDuration: 700,
    tooltip: { trigger: 'item', backgroundColor: '#202631', borderColor: '#343D4C', textStyle: { color: '#E8EDF5' }, formatter: '{b}<br/>{c} 次 ({d}%)' },
    title: { text: stats.value.total.toLocaleString(), subtext: '执行总数', left: 'center', top: 'center', textStyle: { color: '#F1F5F9', fontSize: 25, fontWeight: 650 }, subtextStyle: { color: '#8995A7', fontSize: 11, lineHeight: 24 } },
    series: [{ type: 'pie', radius: ['65%', '82%'], center: ['50%', '50%'], avoidLabelOverlap: true, label: { show: false }, itemStyle: { borderColor: '#1B2029', borderWidth: 4, borderRadius: 5 }, data: items.map(i => ({ name: i.name, value: i.count, itemStyle: { color: i.color } })) }],
  })

  charts[2]?.setOption({
    animationDuration: 700,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#202631', borderColor: '#343D4C', textStyle: { color: '#E8EDF5' } },
    grid: { left: 4, right: 30, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', show: false },
    yAxis: { type: 'category', inverse: true, data: topJobs.value.map(j => j.name), axisLabel: { color: '#AAB4C4', fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{ type: 'bar', data: topJobs.value.map(j => j.runs), barWidth: 9, showBackground: true, backgroundStyle: { color: '#2A313D', borderRadius: 6 }, itemStyle: { color: '#6C9CFF', borderRadius: [0, 5, 5, 0] }, label: { show: true, position: 'right', color: '#C7D0DE', fontSize: 11 } }],
  })

  charts[3]?.setOption({
    animationDuration: 700,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#202631', borderColor: '#343D4C', textStyle: { color: '#E8EDF5' }, formatter: params => `${params[0].name}<br/>成功率：${params[0].value}%` },
    grid: { left: 4, right: 35, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', max: 100, show: false },
    yAxis: { type: 'category', inverse: true, data: healthJobs.value.map(j => j.name), axisLabel: { color: '#AAB4C4', fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{ type: 'bar', data: healthJobs.value.map(j => ({ value: j.rate, itemStyle: { color: j.rate >= 95 ? '#43C99A' : j.rate >= 80 ? '#E7B84B' : '#EF6A70' } })), barWidth: 9, showBackground: true, backgroundStyle: { color: '#2A313D', borderRadius: 6 }, itemStyle: { borderRadius: [0, 5, 5, 0] }, label: { show: true, position: 'right', formatter: '{c}%', color: '#C7D0DE', fontSize: 11 } }],
  })
}

function resizeCharts() { charts.forEach(chart => chart?.resize()) }

async function load() {
  try {
    const res = await fetchHistory({ all: true })
    const runs = res.items || []
    const s = aggregate(runs)
    s.runsFull = runs
    stats.value = s
    await nextTick()
    makeCharts()
  } catch (e) {
    unloadMsg.value = e?.message || String(e)
  }
}

watch(activeRange, async () => { await nextTick(); makeCharts() })
watch(refreshTick, () => load())

onMounted(() => {
  load()
  window.addEventListener('resize', resizeCharts)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  charts.forEach(chart => chart?.dispose())
  charts = []
})
</script>

<template>
  <div v-loading="loading" class="dash">

    <section class="page-heading"><div class="eyebrow">OVERVIEW / MONITORING</div></section>

    <section class="kpi-grid">
      <article v-for="kpi in kpis" :key="kpi.label" class="kpi-card">
        <div class="kpi-top"><span>{{ kpi.label }}</span><span class="kpi-icon" :class="kpi.tone">{{ kpi.icon }}</span></div>
        <div class="kpi-value-row"><strong>{{ kpi.value }}</strong><span class="delta" :class="kpi.tone">{{ kpi.delta }}</span></div>
        <div class="kpi-bottom"><span>{{ kpi.note }}</span>
          <span class="mini-spark" :class="kpi.tone">
            <svg viewBox="0 0 72 22" preserveAspectRatio="none"><polyline points="0,18 10,14 20,17 30,8 40,12 50,4 60,9 72,2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>
        </div>
      </article>
    </section>

    <section class="primary-grid">
      <article class="panel trend-panel">
        <div class="panel-heading">
          <div><h2>执行趋势</h2><p>任务运行量与异常情况</p></div>
          <div class="range-switch">
            <button v-for="range in ranges" :key="range.label" :class="{ selected: activeRange === range.label }" @click="activeRange = range.label">{{ range.label }}</button>
          </div>
        </div>
        <div ref="trendEl" class="trend-chart"></div>
        <div class="chart-foot">
          <span><i class="legend-dot success"></i>成功执行</span>
          <span><i class="legend-dot failed"></i>失败执行</span>
          <span class="foot-note">数据随任务事件实时更新</span>
        </div>
      </article>

      <article class="panel status-panel">
        <div class="panel-heading"><div><h2>执行状态</h2><p>全部任务状态占比</p></div></div>
        <div class="status-content">
          <div ref="statusEl" class="status-chart"></div>
          <div class="status-legend">
            <div v-for="item in statusItems" :key="item.key" class="status-row">
              <span class="status-name"><i :style="{ background: item.color }"></i>{{ item.name }}</span>
              <span class="status-count">{{ item.count.toLocaleString() }}</span>
              <span class="status-percent">{{ item.percent }}%</span>
            </div>
          </div>
        </div>
        <div class="status-footer"><span>任务总量</span><strong>{{ stats.total.toLocaleString() }} <small>次</small></strong></div>
      </article>
    </section>

    <section class="secondary-grid">
      <article class="panel">
        <div class="panel-heading"><div><h2>任务执行排行</h2><p>按执行次数排序 · Top 5</p></div></div>
        <div ref="rankEl" class="small-chart"></div>
      </article>
      <article class="panel">
        <div class="panel-heading"><div><h2>任务健康度</h2><p>各任务成功率表现</p></div></div>
        <div ref="healthEl" class="small-chart"></div>
      </article>
    </section>

    <footer class="page-footer">
      <span>调度中心 · 数据看板</span>
      <span>数据每次事件触发后更新 <i class="pulse"></i></span>
    </footer>
  </div>
</template>

<style scoped>
.dash { font-family: Inter, "Noto Sans SC", -apple-system, "Segoe UI", sans-serif; color: #e8edf5; }

.page-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; padding: 4px 0 23px; }
.eyebrow { color: #75839a; font-size: 9px; letter-spacing: 1.5px; font-weight: 700; margin-bottom: 8px; }

.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 13px; }
.kpi-card { border: 1px solid var(--border); border-radius: 10px; background: var(--panel); padding: 16px 17px 12px; min-width: 0; transition: background .18s, border-color .18s; }
.kpi-card:hover { background: #1d232d; border-color: #3a4555; }
.kpi-top { display: flex; justify-content: space-between; align-items: center; color: #94a0b1; font-size: 11px; }
.kpi-icon { display: grid; place-items: center; width: 25px; height: 25px; border-radius: 7px; font-size: 12px; font-weight: 700; }
.kpi-icon.blue { color: #8bb0ff; background: #6c9cff17; }
.kpi-icon.green { color: #43c99a; background: #43c99a17; }
.kpi-icon.violet { color: #b49aff; background: #a78bfa17; }
.kpi-icon.red { color: #ef7b80; background: #ef6a7017; }
.kpi-value-row { display: flex; align-items: baseline; gap: 10px; margin-top: 10px; }
.kpi-value-row strong { font-size: 27px; font-weight: 650; letter-spacing: -.8px; line-height: 1.2; color: #e8edf5; }
.delta { font-size: 10px; font-weight: 600; }
.delta.blue { color: #7ea8ff; }
.delta.green { color: #43c99a; }
.delta.violet { color: #b49aff; }
.delta.red { color: #ef7b80; }
.kpi-bottom { display: flex; justify-content: space-between; align-items: center; color: #687589; font-size: 9px; margin-top: 10px; }
.mini-spark { width: 55px; height: 18px; }
.mini-spark svg { width: 100%; height: 100%; }
.mini-spark.blue { color: #6c9cff; }
.mini-spark.green { color: #43c99a; }
.mini-spark.violet { color: #a78bfa; }
.mini-spark.red { color: #ef6a70; }

.primary-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(320px, 1fr); gap: 13px; margin-top: 14px; }
.secondary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px; margin-top: 14px; }
.panel { border: 1px solid var(--border); border-radius: 10px; background: var(--panel); min-width: 0; }
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; padding: 17px 18px 0; }
.panel-heading h2 { margin: 0; font-size: 12px; font-weight: 650; letter-spacing: -.1px; color: var(--text); }
.panel-heading p { margin: 6px 0 0; font-size: 10px; color: #7e8a9c; }
.range-switch { display: flex; gap: 2px; border: 1px solid #2a313d; border-radius: 6px; padding: 3px; background: #151a21; }
.range-switch button { color: #7f8b9d; border: 0; border-radius: 4px; background: transparent; padding: 5px 8px; font-size: 9px; white-space: nowrap; cursor: pointer; font: inherit; }
.range-switch button.selected { color: #dce7ff; background: #2b3a52; }
.trend-chart { height: 228px; padding: 0 10px; }
.chart-foot { display: flex; gap: 16px; align-items: center; border-top: 1px solid #262d37; margin: 0 18px; padding: 10px 0 12px; font-size: 9px; color: #8995a7; }
.chart-foot span { display: flex; align-items: center; gap: 6px; }
.legend-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
.legend-dot.success { background: #43c99a; }
.legend-dot.failed { background: #ef6a70; }
.foot-note { margin-left: auto; color: #657185; }
.status-content { display: flex; align-items: center; gap: 5px; padding: 7px 17px 0; }
.status-chart { width: 46%; height: 190px; min-width: 145px; }
.status-legend { flex: 1; min-width: 0; display: grid; gap: 12px; }
.status-row { display: grid; grid-template-columns: minmax(65px, 1fr) auto auto; gap: 9px; align-items: center; font-size: 10px; }
.status-name { display: flex; align-items: center; gap: 7px; color: #9ca8b8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.status-name i { width: 8px; height: 8px; border-radius: 3px; flex-shrink: 0; }
.status-count { color: #cdd6e2; font-family: "DM Mono", monospace; }
.status-percent { color: #8995a7; width: 38px; text-align: right; }
.status-footer { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #262d37; margin: 10px 18px 0; padding: 12px 0 14px; font-size: 10px; color: #8995a7; }
.status-footer strong { color: #e8edf5; font-size: 16px; }
.status-footer small { color: #657186; font-size: 10px; }

.small-chart { height: 205px; padding: 7px 12px 10px; }
.page-footer { display: flex; justify-content: space-between; align-items: center; color: #586578; font-size: 9px; padding: 18px 2px 2px; }
.page-footer span:last-child { display: flex; align-items: center; gap: 8px; }
.pulse { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #43c99a; box-shadow: 0 0 0 4px #43c99a1a; flex-shrink: 0; }

@media (min-width: 1500px) { .kpi-card { padding: 19px 20px 14px; }.trend-chart { height: 250px; }.status-chart { height: 205px; }.small-chart { height: 225px; } }
@media (max-width: 1100px) { .kpi-grid { gap: 9px; }.kpi-card { padding: 13px; }.kpi-value-row strong { font-size: 23px; }.status-content { padding-left: 10px; padding-right: 10px; }.status-chart { min-width: 125px; }.status-legend { gap: 9px; } }
@media (max-width: 850px) { .page-heading { align-items: flex-start; }.kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.primary-grid { grid-template-columns: 1fr; }.status-content { justify-content: center; }.status-chart { width: 40%; max-width: 220px; }.status-legend { max-width: 330px; }.secondary-grid { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .page-heading { display: block; padding: 23px 0 18px; }.kpi-grid { gap: 8px; }.kpi-card { padding: 12px; }.kpi-value-row { flex-wrap: wrap; gap: 6px; }.kpi-value-row strong { font-size: 22px; }.mini-spark { width: 42px; }.panel-heading { padding: 14px 13px 0; }.range-switch button { padding: 5px 6px; font-size: 8px; }.trend-chart { height: 205px; }.chart-foot { margin: 0 13px; gap: 11px; }.foot-note { display: none; }.status-content { padding: 7px 10px 0; gap: 0; }.status-chart { width: 43%; min-width: 125px; height: 170px; }.status-legend { gap: 8px; }.status-row { gap: 5px; font-size: 9px; }.status-percent { width: 32px; }.status-footer { margin: 0 13px; }.small-chart { height: 195px; } }
</style>