<script setup>
import JobForm from './JobForm.vue'
defineProps({
  title: String,
  schemas: { type: Object, default: () => ({}) },
  initial: { type: Object, default: null },
  readonly: { type: Boolean, default: false },
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
.job-edit-drawer :deep(.el-drawer__body) {
  overflow-y: auto;
}
</style>