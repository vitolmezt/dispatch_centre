import { reactive } from 'vue'

/**
 * 全局动作结果提示（Toast）。
 * 需求：任何「执行动作」结束——启动成功/失败、中止、任务完成——都在屏幕中心上方
 * 快速弹出一个框，只显示动作的结果（成功/失败/已阻止），可自动消失。
 *
 * 用法：
 *   import { toast } from '@/store/toast'
 *   toast.success('执行完成')
 *   toast.error('执行失败')
 *   toast.info('已阻止：任务正在运行')
 */

let seq = 0
const toasts = reactive([])
const DEFAULT_MS = 2600

export function pushToast(message, { type = 'info', ms = DEFAULT_MS } = {}) {
  const id = ++seq
  toasts.push({ id, message, type })
  if (ms && ms > 0) {
    setTimeout(() => dismiss(id), ms)
  }
  return id
}

export function dismiss(id) {
  const i = toasts.findIndex(t => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

export const toast = {
  success: (msg, opt) => pushToast(msg, { ...opt, type: 'success' }),
  error: (msg, opt) => pushToast(msg, { ...opt, type: 'error' }),
  info: (msg, opt) => pushToast(msg, { ...opt, type: 'info' }),
}

export { toasts }
export default { toasts, pushToast, dismiss, toast }