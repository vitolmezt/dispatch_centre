import { ref } from 'vue'

const THEME_KEY = 'scheduler-center-theme'
const DEFAULT_THEME = 'dark'

const current = ref('')

const matches = el => !!el

function load() {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch (e) { /* localStorage 不可用时回退默认 */ }
  return DEFAULT_THEME
}

function apply(t, persist = false) {
  current.value = t
  const el = document.documentElement
  el.setAttribute('data-theme', t)
  if (t === 'dark') el.classList.add('dark')
  else el.classList.remove('dark')
  if (persist) {
    try { localStorage.setItem(THEME_KEY, t) } catch (e) { /* ignore */ }
  }
}

export function initTheme() {
  apply(load())
}

export function setTheme(t) {
  const next = t === 'light' ? 'light' : 'dark'
  apply(next, true)
  return next
}

export function toggleTheme() {
  const t = current.value || load()
  return setTheme(t === 'dark' ? 'light' : 'dark')
}

export function useTheme() {
  return { theme: current }
}

export function getThemeVar() {
  return current.value || load()
}

export default { initTheme, setTheme, toggleTheme, useTheme, THEME_KEY, DEFAULT_THEME }