import { ref, computed, onUnmounted, nextTick } from 'vue'

const HEADER_H = 48
const MIN_ROW_H = 44
const MAX_ROW_H = 72

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

  const rowHeight = computed(() => {
    const rows = fillRef.value
    if (!rows) return MIN_ROW_H
    const bodyH = Math.max(0, tableHeight.value - HEADER_H)
    const n = Math.min(rows, Math.max(1, Math.ceil(bodyH / MIN_ROW_H)))
    const h = Math.floor(bodyH / n)
    return Math.min(MAX_ROW_H, Math.max(MIN_ROW_H, h))
  })
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