<script setup>
import JobForm from './JobForm.vue'

/**
 * 任务新增/编辑弹窗（居中 Modal）。
 * 内部使用 JobForm 动态渲染参数表单。
 * readonly=true 时进入只读查看模式：表单禁用、隐藏操作按钮，仅展示配置。
 */
defineProps({
  title: String,
  schemas: { type: Object, default: () => ({}) },
  initial: { type: Object, default: null },
  readonly: { type: Boolean, default: false },   // 只读查看模式（不提交、不保存）
})
defineEmits(['close', 'submit'])
</script>

<template>
  <teleport to="body">
    <el-dialog :model-value="true" :title="title" width="min(640px, 94vw)"
               :close-on-click-modal="true" append-to-body class="job-edit-dialog"
               @close="$emit('close')">
      <JobForm :schemas="schemas" :initial="initial" :readonly="readonly"
               @submit="p => $emit('submit', p)"
               @cancel="$emit('close')" />
    </el-dialog>
  </teleport>
</template>

<style scoped>
:deep(.el-dialog__body) { padding-top: 8px !important; }
/* 标题居中 */
:deep(.el-dialog__header) { text-align: center !important; }
/* 弹窗整体不超高：限高到视口 90%，内容超高时 body 内部滚动，避免越过屏幕底部 */
.job-edit-dialog :deep(.el-dialog) { max-height: 90vh; margin: 5vh auto 0 !important; }
.job-edit-dialog :deep(.el-dialog__body) {
  overflow-y: auto; max-height: calc(90vh - 120px);
  padding-top: 8px !important;
}
</style>