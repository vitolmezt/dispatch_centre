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
    <div class="modal-root" @click.self="$emit('close')">
      <div class="modal">
        <header class="m-head">
          <span class="m-title">{{ title }}</span>
          <button class="icon-btn" @click="$emit('close')">✕</button>
        </header>
        <div class="m-body">
          <JobForm :schemas="schemas" :initial="initial"
                   @submit="p => $emit('submit', p)"
                   @cancel="$emit('close')" />
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped>
.modal-root {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0,0,0,.5);
  display: grid; place-items: center;
}
.modal {
  width: min(560px, 94vw);
  max-height: 88vh; overflow: auto;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; box-shadow: 0 18px 50px rgba(0,0,0,.6);
}
.m-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: var(--panel); z-index: 2;
}
.m-title { font-size: 16px; font-weight: 700; }
.icon-btn { background: transparent; border: none; color: var(--muted); font-size: 16px; padding: 4px 8px; border-radius: 6px; }
.icon-btn:hover { color: var(--text); background: var(--panel-2); }
.m-body { padding: 18px 20px; }
</style>