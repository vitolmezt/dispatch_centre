import { getThemeVar } from './useTheme'


function cssVar(name, fallback = '') {
  if (typeof window === 'undefined' || !window.getComputedStyle) return fallback
  try {
    const cs = window.getComputedStyle(document.documentElement)
    void cs.backgroundColor
    const v = cs.getPropertyValue(name).trim()
    return v || fallback
  } catch (e) {
    return fallback
  }
}

export function getChartThemeOptions() {
  const isDark = getThemeVar() === 'dark'
  return {

    tooltip: {
      backgroundColor: cssVar('--tooltip-bg', isDark ? '#20242d' : '#ffffff'),
      borderColor: cssVar('--tooltip-border', isDark ? '#3a4555' : '#cbd5e1'),
      textStyle: { color: cssVar('--tooltip-text', isDark ? '#ffffff' : '#0f172a') },
    },
    axisLabelColor: cssVar('--text-muted', isDark ? '#9AA5B5' : '#64748b'),
    gridLineColor: isDark ? 'rgba(148, 163, 184, .12)' : 'rgba(100, 116, 139, .16)',
    barBgColor: cssVar('--bg-panel-2', isDark ? '#2A313D' : '#eef2f7'),
    pieBorderColor: isDark ? '#1B2029' : '#ffffff',
    titleColor: cssVar('--text-primary', isDark ? '#F1F5F9' : '#0f172a'),
    subTitleColor: cssVar('--text-muted', isDark ? '#8995A7' : '#64748b'),
    axisLabelColorStrong: cssVar('--text-secondary', isDark ? '#AAB4C4' : '#475569'),
    seriesLabelColor: cssVar('--text-secondary', isDark ? '#C7D0DE' : '#475569'),
  }
}

// 与看板 KPI 图标、图例圆点、状态分布饼图保持同一组成功/失败色
const STATE_COLORS = { ok: '#43c99a', fail: '#ef6a70' }

export const chartStateColor = (k) => STATE_COLORS[k] || STATE_COLORS.ok

export { cssVar }
