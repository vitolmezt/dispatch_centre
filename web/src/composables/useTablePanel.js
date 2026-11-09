import { ref, computed, onUnmounted, nextTick } from 'vue'

// 表头 + 边框的估算高度（像素），行高铺满时据此扣除
const HEADER_H = 48
const MIN_ROW_H = 44
const MAX_ROW_H = 72

// 共享的"自适应表格高度 + 行高铺满"逻辑：
//  - 高度：让表格铺满父面板剩余空间（父需为 flex 纵向容器，且包含
//    <el-table ref="panelRef"> 与底部 <div ref="footerRef">）。Element Plus 原生 height
//    方案 + ResizeObserver 实时重算；即使数据很少，表格外框/表头也拉伸到底部。
//  - 行高：把 fill（每页固定行数，默认跟随 pageSize）铺满表格 body——动态均分行高，
//    不滚动、也不在表格与页脚之间留一大段空白。
//
// fill 可以是 `number` / `ref` / `getter函数`（如 () => props.fill || props.pageSize，
// getter 每次被行高 computed 读取时实时求值，条数切换自动重算）。
export function useTablePanel(fill = 0) {
  const panelRef = ref(null)
  const footerRef = ref(null)
  const tableHeight = ref(300)
  let ro = null

  const fillRef = (typeof fill === 'function')
    ? { get value() { return fill() } }
    : (typeof fill === 'object' && fill !== null) ? { get value() { return fill.value } } : { get value() { return fill } }

  function computeTableHeight() {
    nextTick(() => {
      if (!panelRef.value) return
      const totalH = panelRef.value.getBoundingClientRect().height
      const footH = footerRef.value ? footerRef.value.offsetHeight : 0
      tableHeight.value = Math.max(120, totalH - footH)
    })
  }

  // 动态行高：让 fillRef 行恰好铺满 body，夹在 [MIN,MAX] 区间防止过空/过挤
  const rowHeight = computed(() => {
    const rows = fillRef.value
    if (!rows) return MIN_ROW_H
    const bodyH = Math.max(0, tableHeight.value - HEADER_H)
    const n = Math.min(rows, Math.max(1, Math.ceil(bodyH / MIN_ROW_H)))
    const h = Math.floor(bodyH / n)
    return Math.min(MAX_ROW_H, Math.max(MIN_ROW_H, h))
  })
  // 注入到 el-table 的 CSS 变量（模板里 v-bind 到 style）
  const tableStyle = computed(() => ({ '--row-h': rowHeight.value + 'px' }))

  function bindTableResize() {
    computeTableHeight()
    if (typeof ResizeObserver !== 'undefined' && panelRef.value) {
      ro = new ResizeObserver(() => computeTableHeight())
      ro.observe(panelRef.value)
    }
    window.addEventListener('resize', computeTableHeight)
  }

  function unbindTableResize() {
    if (ro) ro.disconnect()
    window.removeEventListener('resize', computeTableHeight)
  }

  return {
    panelRef, footerRef, tableHeight, tableStyle, rowHeight,
    computeTableHeight, bindTableResize, unbindTableResize,
  }
}