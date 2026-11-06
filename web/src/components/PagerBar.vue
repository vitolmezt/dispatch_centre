<script setup>
// 分页页脚：统一基于 el-pagination，仅承载"页码变化 -> 重新加载"的桥梁。
// 保持原 props/emit 名不变，三个列表页无需改动即可接入。
// 可选：传入 withSizes 开启"每页条数"切换（layout 增加 sizes + sizes 通知）。
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