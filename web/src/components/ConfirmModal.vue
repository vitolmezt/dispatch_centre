<script setup>
// 极简确认弹窗：是否执行某操作。
defineProps({
  show: Boolean,
  title: { type: String, default: '确认' },
  message: { type: String, default: '' },
  loading: Boolean,
})
const emit = defineEmits(['ok', 'cancel'])
</script>

<template>
  <teleport to="body">
    <transition name="fade">
      <div v-if="show" class="mask" @click.self="emit('cancel')">
        <div class="box">
          <div class="box-title">{{ title }}</div>
          <div class="box-msg">{{ message }}</div>
          <div class="box-actions">
            <button @click="emit('cancel')">取消</button>
            <button class="primary" :disabled="loading" @click="emit('ok')">
              {{ loading ? '执行中…' : '确认执行' }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.mask {
  position: fixed; inset: 0; z-index: 2000; background: rgba(0,0,0,.4);
  display: grid; place-items: center;
}
.box {
  min-width: 320px; max-width: 420px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px 22px; box-shadow: 0 12px 40px rgba(0,0,0,.5);
}
.box-title { font-size: 16px; font-weight: 700; margin-bottom: 10px; }
.box-msg { font-size: 14px; color: var(--muted); margin-bottom: 18px; line-height: 1.6; }
.box-actions { display: flex; justify-content: flex-end; gap: 8px; }
button { background: var(--panel-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 7px 16px; font-size: 13px; }
button.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
button:hover { filter: brightness(1.1); }
button:disabled { opacity: .5; }
.fade-enter-active, .fade-leave-active { transition: opacity .15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>