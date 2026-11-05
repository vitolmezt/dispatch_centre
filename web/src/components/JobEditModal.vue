<script setup>
import JobForm from './JobForm.vue'

/**
 * 指令新增/编辑弹窗（居中 Modal）。
 * 内部使用 JobForm 动态渲染参数表单。
 */
defineProps({
  title: String,
  schemas: { type: Object, default: () => ({}) },
  initial: { type: Object, default: null },
})
defineEmits(['close', 'submit'])
</script>

<template>
  <teleport to="body">
    <el-dialog :model-value="true" :title="title" width="min(640px, 94vw)"
               :close-on-click-modal="true" append-to-body @close="$emit('close')">
      <JobForm :schemas="schemas" :initial="initial"
               @submit="p => $emit('submit', p)"
               @cancel="$emit('close')" />
    </el-dialog>
  </teleport>
</template>

<style scoped>
:deep(.el-dialog__body) { padding-top: 8px !important; }
/* 标题居中 */
:deep(.el-dialog__header) { text-align: center !important; }
</style>