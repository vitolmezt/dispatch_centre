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
  const k = key === true ? 'ON' : key === false ? 'OFF' : key
  return t('ENABLED', k, key == null ? '' : String(key))
}

export { dict as groups }

const _MONTH_NAMES = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
const _DOW_NAMES = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']
const _CN_DOW = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function _parseField(field, rangeMax, names) {
  field = (field || '').trim().toLowerCase()
  if (field === '*' || field === '?') return { type: 'every' }
  const star = field.match(/^\*\/(\d+)$/)
  if (star) return { type: 'everyN', n: parseInt(star[1], 10) }
  const req = field.match(/^(\d+)\/(\d+)$/)
  const vals = []
  for (const part of field.split(',')) {
    const rm = part.match(/^(\w+)-(\w+)(?:\/(\d+))?$/)
    if (rm) {
      const s = _tokenVal(rm[1], names)
      const e = _tokenVal(rm[2], names)
      if (s == null || e == null) continue
      const step = rm[3] ? +rm[3] : 1
      const lo = Math.min(s, e)
      const hi = Math.max(s, e)
      for (let v = lo; v <= hi; v += step) vals.push(v)
      continue
    }
    const tv = _tokenVal(part, names)
    if (tv != null) vals.push(tv)
  }
  if (!vals.length) return { type: 'set', values: [] }
  if (req && vals.length === 0) {
    return { type: 'everyNfrom', n: +req[2], start: +req[1] }
  }
  const uniq = [...new Set(vals)].sort((a, b) => a - b)
  return { type: 'set', values: uniq }
}

function _tokenVal(tok, names) {
  const low = String(tok).toLowerCase()
  if (/^\d+$/.test(low)) return parseInt(low, 10)
  if (names) {
    const idx = names.indexOf(low)
    if (idx >= 0) return idx
  }
  return null
}

function _isAny(f) { return f && f.type === 'every' }

function _timeText(hours, min) {
  const hh = (Array.isArray(hours) ? hours : [hours]).map(h => String(h).padStart(2, '0')).join('/')
  return `${hh}:${String(min).padStart(2, '0')}`
}
function _hoursCtx(ph) {
  if (ph.type === 'set') return { kind: 'set', list: ph.values }
  if (ph.type === 'everyN') return { kind: 'everyN', n: ph.n }
  if (ph.type === 'everyNfrom') return { kind: 'stepFrom', n: ph.n, start: ph.start }
  return { kind: 'every' }
}
function _dowCtx(pw) {
  if (pw.type === 'set') {
    const arr = pw.values
    if (arr.length === 5 && JSON.stringify(arr) === JSON.stringify([1, 2, 3, 4, 5])) return { kind: 'weekdays' }
    if (arr.length === 2 && JSON.stringify(arr) === JSON.stringify([0, 6])) return { kind: 'weekend' }
    return { kind: 'list', list: arr }
  }
  return { kind: 'every' }
}

export function cronToHuman(cron) {
  const s = (cron || '').trim()
  if (!s) return ''
  const f = String(s).split(/\s+/)
  if (f.length !== 5) return null
  const pm = _parseField(f[0], 59, null)
  const ph = _parseField(f[1], 23, null)
  const pd = _parseField(f[2], 31, null)
  const pmo = _parseField(f[3], 12, _MONTH_NAMES)
  const pw = _parseField(f[4], 6, _DOW_NAMES)

  const anyD = _isAny(pd)
  const anyM = _isAny(pmo)
  const anyW = _isAny(pw)

  if (anyD && anyM && anyW && _isAny(ph)) {
    if (pm.type === 'every') return '每分钟'
    if (pm.type === 'everyN') return `每 ${pm.n} 分钟`
    if (pm.type === 'everyNfrom') return `从第 ${pm.start} 分钟起每 ${pm.n} 分钟`
  }

  if (anyD && anyM && anyW && pm.type === 'set' && pm.values.length === 1 && pm.values[0] === 0) {
    const hc = _hoursCtx(ph)
    if (hc.kind === 'everyN') return `每 ${hc.n} 小时整点`
    if (hc.kind === 'stepFrom') return `每天从 ${hc.start} 点起每 ${hc.n} 小时`
    if (hc.kind === 'set') return `每天 ${_timeText(hc.list, 0)}`
    if (hc.kind === 'every') return '每小时整点'
  }

  if (anyD && anyM && anyW && _isAny(ph) && pm.type === 'set') {
    const mm = pm.values.map(v => String(v).padStart(2, '0')).join('、')
    return pm.values.length === 1 ? `每小时 ${String(pm.values[0]).padStart(2, '0')} 分` : `每小时 ${mm} 分`
  }

  const needTime = pm.type === 'set' && ph.type === 'set' && pm.values.length && ph.values.length

  if (needTime && anyD && anyM && anyW) {
    return `每天 ${_timeText(ph.values, pm.values[0])}`
  }
  if (needTime && anyD && anyM && !anyW) {
    const dow = _dowCtx(pw)
    if (dow.kind === 'weekdays') return `每周一至周五 ${_timeText(ph.values, pm.values[0])}`
    if (dow.kind === 'weekend') return `每周六、周日 ${_timeText(ph.values, pm.values[0])}`
    if (dow.kind === 'list') return `每周${dow.list.map(i => _CN_DOW[i]).join('、')} ${_timeText(ph.values, pm.values[0])}`
  }
  if (needTime && !anyD && anyM && anyW) {
    const doms = pd.values.map(v => v + '日').join('、')
    return pd.values.length > 1
      ? `每月${doms} ${_timeText(ph.values, pm.values[0])}`
      : `每月 ${pd.values[0]} 日 ${_timeText(ph.values, pm.values[0])}`
  }
  if (needTime && anyD && !anyM && anyW) {
    const mons = pmo.values.map(v => v + '月').join('、')
    return (pmo.values.length > 1 ? `每年${mons}` : `每年 ${pmo.values[0]} 月`) + ` ${_timeText(ph.values, pm.values[0])}`
  }

  return null
}