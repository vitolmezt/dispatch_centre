import { ref, onMounted, onUnmounted, nextTick } from 'vue'

// 共享的"自适应表格高度"逻辑：让表格高度铺满父面板剩余空间（父需为 flex 纵向容器，
// 且包含 <el-table ref="panelRef"> 与底部 <div ref="footerRef">）。
// 这是 Element Plus 原生 height 方案 + ResizeObserver 实时重算，保证：
//  - 表头固定，body 高度随数据滚动
//  - 即使数据很少，表格外框/表头也一路拉伸到底部，不留大块空白
export function useTablePanel() {
  const panelRef = ref(null)
  const footerRef = ref(null)
  const tableHeight = ref(300)
  let ro = null

  function computeTableHeight() {
    nextTick(() => {
      if (!panelRef.value) return
      const totalH = panelRef.value.getBoundingClientRect().height
      const footH = footerRef.value ? footerRef.value.offsetHeight : 0
      tableHeight.value = Math.max(120, totalH - footH)
    })
  }

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

  // 供各页面在 onMounted / onUnmounted 中调用
  return { panelRef, footerRef, tableHeight, computeTableHeight, bindTableResize, unbindTableResize }
}