import { reactive } from 'vue'
import { fetchConstants } from './api'

/**
 * 全局常量字典（有类型分组）。
 * - 后端 /api/constants 下发：groups = { STATUS: {...}, JOB_TYPE: {...}, TRIGGER: {...}, ... }
 * - 前端所有需要把「英文KEY」翻译成「显示文案」的地方，都用这里的 helper，不再重复硬编码。
 * - 使用方式：
 *     import { dict, t, statusLabel, jobTypeLabel } from '@/constants'
 *     statusLabel('RUNNING')  // => '进行中'
 */
export const dict = reactive({ groups: {} })
export const capabilities = reactive({ fsPick: false })

export async function loadConstants() {
  const data = await fetchConstants()
  dict.groups = data?.groups || {}
  if (data?.capabilities) {
    capabilities.fsPick = !!data.capabilities.fsPick
  } else {
    capabilities.fsPick = false
  }
}

/** 按分组取文案；KEY 缺失或分组缺失时回退策略：返回英文原样或默认值. */
export function t(group, key, fallback = '') {
  const g = dict.groups?.[group]
  if (g && key in g) return g[key]
  return fallback || (key ?? '')
}

/** 状态快捷映射. */
export function statusLabel(key) {
  return t('STATUS', key, key)
}
export function jobTypeLabel(key) {
  return t('JOB_TYPE', key, key)
}
export function triggerLabel(key) {
  return t('TRIGGER', key, key)
}
export function enabledLabel(key) {
  // 输入为 boolean 或 'ON'/'OFF'
  const k = key === true ? 'ON' : key === false ? 'OFF' : key
  return t('ENABLED', k, key == null ? '' : String(key))
}

export { dict as groups }