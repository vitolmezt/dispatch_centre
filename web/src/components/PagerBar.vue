<script setup>
// 分页页脚：统一基于 el-pagination，仅承载"页码变化 -> 重新加载"的桥梁。
// 保持原 props/emit 名不变，三个列表页无需改动即可接入。
const props = defineProps({
  page: { type: Number, default: 1 },
  total: { type: Number, default: 0 },
  pageSize: { type: Number, default: 10 },
})
const emit = defineEmits(['change'])

function onCurrentChange(p) {
  emit('change', p)
}
</script>

<template>
  <div class="pager">
    <el-pagination background layout="prev, pager, next, total"
                   :current-page="page" :page-size="pageSize" :total="total"
                   @current-change="onCurrentChange" />
  </div>
</template>

<style scoped>
.pager {
  display: flex; align-items: center; justify-content: center;
  padding: 12px 4px;
}
</style>