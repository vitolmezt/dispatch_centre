import { ElMessage } from 'element-plus'

/**
 * 全局动作结果提示（Toast）——基于 Element Plus 的 ElMessage。
 * 旧实现是自研 store + ToastHost 渲染；统一到 ElMessage 后，
 * 各页面仍通过同一接口调用，无需改动调用处：
 *   import { toast } from '@/store/toast'
 *   toast.success('执行完成')
 *   toast.error('执行失败')
 *   toast.info('已阻止：任务正在运行')
 */
export const toast = {
  success: (msg, opt) => ElMessage.success(msg, opt),
  error: (msg, opt) => ElMessage.error(msg, opt),
  info: (msg, opt) => ElMessage.info(msg, opt),
}

export default toast