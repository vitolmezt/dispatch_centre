<script setup>
const props = defineProps({
  page: { type: Number, default: 1 },
  total: { type: Number, default: 0 },
  pageSize: { type: Number, default: 10 },
  withSizes: { type: Boolean, default: false },
  sizeOptions: { type: Array, default: () => [10, 15, 30] },
})
const emit = defineEmits(['change', 'size-change'])

function onCurrentChange(p) {
  emit('change', p)
}
function onSizeChange(s) {
  emit('size-change', s)
}
const layout = props.withSizes ? 'sizes, prev, pager, next, total' : 'prev, pager, next, total'
</script>

<template>
  <div class="pager">
    <el-pagination background :layout="layout"
                   :current-page="page" :page-size="pageSize" :total="total"
                   :page-sizes="sizeOptions"
                   @current-change="onCurrentChange" @size-change="onSizeChange" />
  </div>
</template>

<style scoped>
.pager {
  display: flex; align-items: center; justify-content: center;
  padding: 12px 4px;
}
</style>