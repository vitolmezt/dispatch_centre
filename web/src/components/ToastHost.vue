<script setup>
import { toasts } from '../store/toast'

/**
 * 全局动作结果提示：屏幕中心上方快速弹出，只显示动作结果。
 * 由 App.vue 挂载一次；任何页面通过 toast.success/error/info 调用。
 */
</script>

<template>
  <teleport to="body">
    <div class="toast-host">
      <transition-group name="toast">
        <div v-for="t in toasts" :key="t.id" :class="['toast', t.type]">
          <span class="dot"></span>
          <span class="msg">{{ t.message }}</span>
        </div>
      </transition-group>
    </div>
  </teleport>
</template>

<style scoped>
.toast-host {
  position: fixed; top: 0; left: 0; right: 0;
  z-index: 3000;
  display: flex; flex-direction: column; align-items: center;
  gap: 10px; padding-top: 18px;
  pointer-events: none;
}
.toast {
  display: flex; align-items: center; gap: 8px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 10px 28px rgba(0,0,0,.5);
  padding: 10px 18px;
  font-size: 14px; font-weight: 600;
  max-width: 70vw;
}
.toast .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.toast.msg { white-space: pre-wrap; word-break: break-word; }

.toast.success { border-color: #1d6540; }
.toast.success .dot { background: var(--ok); }
.toast.success .msg { color: #7fe0a6; }

.toast.error { border-color: #7a2f32; }
.toast.error .dot { background: var(--danger); }
.toast.error .msg { color: #ff8b8e; }

.toast.info { border-color: #31557e; }
.toast.info .dot { background: var(--primary); }
.toast.info .msg { color: #a8c7ff; }

/* 弹出/消失动画：从顶部滑入 + 淡入 */
.toast-enter-active { transition: all .25s ease; }
.toast-leave-active { transition: all .18s ease; }
.toast-enter-from { transform: translateY(-14px); opacity: 0; }
.toast-leave-to { transform: translateY(-10px); opacity: 0; }
.toast-move { transition: transform .25s ease; }
</style>