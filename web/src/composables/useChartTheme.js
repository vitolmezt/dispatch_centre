
import { getThemeVar } from './useTheme'

/**
 * 读取当前主题下的 CSS 变量值（作用于 :root / html[data-theme]）。
 * @returns {string}
 */
function cssVar(name, fallback = '') {
  if (typeof window === 'undefined' || !window.getComputedStyle) return fallback
  try {
    const v = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return v || fallback
  } catch (e) {
    return fallback
  }
}


export function getChartThemeOptions() {
  const isDark = getThemeVar() === 'dark'
  return {
    tooltip: {
      backgroundColor: cssVar('--tooltip-bg', isDark ? '#20242d' : '#0f172a'),
      borderColor: cssVar('--border-color', isDark ? '#343D4C' : '#e2e8f0'),
      textStyle: { color: cssVar('--text-primary', isDark ? '#E8EDF5' : '#0f172a') },
    },
    axisLabelColor: cssVar('--text-muted', isDark ? '#9AA5B5' : '#64748b'),
    gridLineColor: isDark
      ? 'rgba(148, 163, 184, .12)'
      : 'rgba(100, 116, 139, .16)',
    barBgColor: cssVar('--bg-panel-2', isDark ? '#2A313D' : '#eef2f7'),
    pieBorderColor: isDark ? '#1B2029' : '#ffffff',
    titleColor: cssVar('--text-primary', isDark ? '#F1F5F9' : '#0f172a'),
    subTitleColor: cssVar('--text-muted', isDark ? '#8995A7' : '#64748b'),
    axisLabelColorStrong: cssVar('--text-secondary', isDark ? '#AAB4C4' : '#475569'),
    seriesLabelColor: cssVar('--text-secondary', isDark ? '#C7D0DE' : '#475569'),
  }
}

const STATE_COLORS = {
  ok: '#55CDA2',
  fail: '#F0747A',
}

export const chartStateColor = (k) => STATE_COLORS[k] || '#55CDA2'

export { cssVar }