<script setup>
import { useAttrs, onMounted, onUnmounted } from 'vue'
import { useTablePanel } from '../composables/useTablePanel'
import PagerBar from './PagerBar.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  page: { type: Number, default: 1 },
  total: { type: Number, default: 0 },
  pageSize: { type: Number, default: 10 },
  withSizes: { type: Boolean, default: false },
  fill: { type: Number, default: 0 },
  clickable: { type: Boolean, default: false },
})

const emit = defineEmits(['change', 'size-change'])

const attrs = useAttrs()
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
.table-panel :deep(.el-table__row) { height: var(--row-h, 44px); }
.table-panel :deep(.el-table__header-wrapper th) { background-color: var(--panel-2); }
.table-panel :deep(.el-table__cell) { border-bottom: 1px solid var(--border); }
.table-panel :deep(.el-table__row:hover > td.el-table__cell) { background-color: var(--table-hover-bg) !important; }
.table-panel :deep(.el-table__empty-block) { border-top: 1px solid var(--border); }
.table-panel.clickable :deep(.el-table__row) { cursor: pointer; }
.table-footer { flex-shrink: 0; }
</style>