<script setup>
// 通用「自适应表格面板」：统一封装 表格容器 + 高度自适应 + 行高铺满 + 底部分页。
// 页面只需：
//   1) 传 el-table 的属/事件（:data / v-loading / @sort-change … 通过 $attrs 自动透传到 el-table）
//   2) 传分页相关 props（page / total / pageSize / withSizes）
//   3) 在默认插槽里写 <el-table-column> …
// 行高铺满：传 fill（每页固定行数，如 10）→ 行高自动均分铺满表格、不滚动、不留大白底。
//
// 本组件是 el-table 的"外壳"，不改动列结构；列如何渲染完全由页面通过插槽控制，
// 因此任务/执行中心/历史/服务组等任何列表页都能直接接入，新列表页也照抄即可。
import { defineProps, useAttrs, onMounted, onUnmounted } from 'vue'
import { useTablePanel } from '../composables/useTablePanel'
import PagerBar from './PagerBar.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  // 分页
  page: { type: Number, default: 1 },
  total: { type: Number, default: 0 },
  pageSize: { type: Number, default: 10 },
  // sizes 仅在有分页条数切换需求时开启
  withSizes: { type: Boolean, default: false },
  // 行高铺满：传 >0 的固定行数则按它均分铺满表格；默认跟随每页条数(pageSize)
  fill: { type: Number, default: 0 },
  // 行可点击（配合 @row-click）：给行加 pointer 光标
  clickable: { type: Boolean, default: false },
})

const emit = defineEmits(['change', 'size-change'])

const attrs = useAttrs()
// fill 未显式给(<=0)时，行高铺满条数跟随 pageSize，条数切换时自动重算
const { panelRef, footerRef, tableHeight, tableStyle, bindTableResize, unbindTableResize } =
  useTablePanel(() => props.fill || props.pageSize)

defineExpose({ computeTableHeight: () => {} })
onMounted(() => bindTableResize())
onUnmounted(() => unbindTableResize())
</script>

<template>
  <div class="table-panel" :class="{ clickable }" ref="panelRef">
    <el-table v-bind="attrs" :height="tableHeight" :style="tableStyle">
      <slot />
    </el-table>
    <div class="table-footer" ref="footerRef">
      <PagerBar :page="page" :total="total" :page-size="pageSize" :with-sizes="withSizes"
                @change="e => emit('change', e)" @size-change="e => emit('size-change', e)" />
    </div>
  </div>
</template>

<style scoped>
.table-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel);
  overflow: hidden;
}
.table-panel :deep(.el-table) { flex: 1; min-height: 0; width: 100%; }
.table-panel :deep(.el-table__inner-wrapper) { height: 100%; }
/* 行高由 --row-h 动态填充（fill 行铺满不滚动），默认 44px */
.table-panel :deep(.el-table__row) { height: var(--row-h, 44px); }
/* 通用暗色表格样式：表头底色、细分隔线、悬停变色、空态分隔条 */
.table-panel :deep(.el-table__header-wrapper th) { background-color: var(--panel-2); }
.table-panel :deep(.el-table__cell) { border-bottom: 1px solid var(--border); }
.table-panel :deep(.el-table__row:hover > td.el-table__cell) { background-color: #262c37 !important; }
.table-panel :deep(.el-table__empty-block) { border-top: 1px solid var(--border); }
.table-panel.clickable :deep(.el-table__row) { cursor: pointer; }
.table-footer { flex-shrink: 0; }
</style>