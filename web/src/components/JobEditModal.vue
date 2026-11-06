<script setup>
import JobForm from './JobForm.vue'

/**
 * 任务新增/编辑抽屉（右侧，与任务详情抽屉统一风格）。
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
    <el-drawer :model-value="true" :title="title" size="660px"
               :show-close="true" :with-header="true" append-to-body
               class="job-edit-drawer" @close="$emit('close')">
      <JobForm :schemas="schemas" :initial="initial" :readonly="readonly"
               @submit="p => $emit('submit', p)"
               @cancel="$emit('close')" />
    </el-drawer>
  </teleport>
</template>

<style scoped>
/* 抽屉正文限高滚动：内容超高时表单区滚动，底部操作按钮保持可见 */
.job-edit-drawer :deep(.el-drawer__body) {
  overflow-y: auto;
}
</style>