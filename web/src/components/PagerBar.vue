<script setup>
import { computed } from 'vue'

// 分页页脚：上一页 / 下一页，支持"下一页查询"。
const props = defineProps({
  page: { type: Number, default: 1 },
  total: { type: Number, default: 0 },
  pageSize: { type: Number, default: 50 },
})
const emit = defineEmits(['change'])

const pages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const hasPrev = computed(() => props.page > 1)
const hasNext = computed(() => props.page < pages.value)

function go(p) {
  if (p < 1 || p > pages.value) return
  emit('change', p)
}
</script>

<template>
  <div class="pager">
    <button :disabled="!hasPrev" @click="go(page - 1)">‹ 上一页</button>
    <span class="count">共 {{ total }} 条 · 第 {{ page }}/{{ pages }} 页</span>
    <button :disabled="!hasNext" @click="go(page + 1)">下一页 ›</button>
  </div>
</template>

<style scoped>
/* 上/下一页紧挨计数文字左右，整体在页脚内居中 */
.pager {
  display: flex; align-items: center; justify-content: center;
  gap: 12px; padding: 12px 4px; font-size: 13px;
}
.count { color: var(--text); white-space: nowrap; }
button {
  background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
  border-radius: 7px; padding: 6px 14px; font-size: 13px; cursor: pointer;
}
button:hover:not(:disabled) { background: var(--primary); color: #fff; }
button:disabled { opacity: .4; cursor: not-allowed; }
</style>