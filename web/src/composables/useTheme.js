import { ref } from 'vue'

const THEME_KEY = 'scheduler-center-theme'
const DEFAULT_THEME = 'dark'

const RIPPLE_DURATION = 550
const RIPPLE_EASING = 'cubic-bezier(0.16, 1, 0.3, 1)'
const RIPPLE_OPACITY_FROM = 0.85
const RIPPLE_BORDER_FROM = 2

const current = ref('')

export const isTransitioning = ref(false)

function load() {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch (e) { /* ignore */ }
  return DEFAULT_THEME
}


function apply(t, persist = false) {
  const el = document.documentElement
  el.setAttribute('data-theme', t)

  if (t === 'dark') el.classList.add('dark')
  else el.classList.remove('dark')

  if (persist) {
    try { localStorage.setItem(THEME_KEY, t) } catch (e) { /* ignore */ }
  }

  current.value = t
}

function rippleAllowed() {
  if (typeof document === 'undefined' || typeof requestAnimationFrame !== 'function') return false
  if (typeof Element === 'undefined' || typeof Element.prototype.animate !== 'function') return false
  try {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false
  } catch (e) { /* ignore */ }
  if (document.hidden === true) return false
  return true
}

function getRippleGeometry(trigger) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  let x = vw / 2
  let y = vh / 2
  try {
    const rect = trigger && trigger.getBoundingClientRect ? trigger.getBoundingClientRect() : null
    if (rect && (rect.width || rect.height)) {
      x = rect.left + rect.width / 2
      y = rect.top + rect.height / 2
    }
  } catch (e) { /* ignore */ }
  const endRadius = Math.hypot(Math.max(x, vw - x), Math.max(y, vh - y))
  return { x, y, endRadius }
}

let rippleEl = null
let rippleAnim = null

function destroyRipple() {
  try { if (rippleAnim) rippleAnim.cancel() } catch (e) { /* ignore */ }
  rippleAnim = null
  try { if (rippleEl && rippleEl.parentNode) rippleEl.parentNode.removeChild(rippleEl) } catch (e) { /* ignore */ }
  rippleEl = null
  isTransitioning.value = false
}

function playRipple(geo) {
  if (!rippleAllowed()) {
    destroyRipple()
    return
  }


  let el = null
  try {
    el = document.createElement('div')
    el.className = 'theme-ripple'
    el.style.left = `${geo.x}px`
    el.style.top = `${geo.y}px`
    document.body.appendChild(el)
    rippleEl = el
    isTransitioning.value = true
  } catch (e) {
    destroyRipple()
    return
  }

  const finalSize = Math.max(0, geo.endRadius * 2)
  let anim = null
  try {
    anim = el.animate(
      [
        { width: '0px', height: '0px', opacity: RIPPLE_OPACITY_FROM, borderWidth: `${RIPPLE_BORDER_FROM}px` },
        { width: `${finalSize}px`, height: `${finalSize}px`, opacity: 0, borderWidth: '0px' },
      ],
      { duration: RIPPLE_DURATION, easing: RIPPLE_EASING, fill: 'forwards' }
    )
    rippleAnim = anim
  } catch (e) {
    destroyRipple()
    return
  }

  const cleanup = () => {
    if (rippleAnim !== anim) return
    try { anim.cancel() } catch (e) { /* ignore */ }
    rippleAnim = null
    try { if (el && el.parentNode) el.parentNode.removeChild(el) } catch (e) { /* ignore */ }
    if (rippleEl === el) rippleEl = null
    isTransitioning.value = false
  }
  Promise.resolve(anim.finished).then(cleanup, cleanup)
}

function switchTheme(next, trigger) {

  if (next === (current.value || load())) return next

  const geo = getRippleGeometry(trigger)

  apply(next, true)

  playRipple(geo)

  return next
}

export function initTheme() {
  apply(load())
}

export function setTheme(t, trigger) {
  return switchTheme(t === 'light' ? 'light' : 'dark', trigger || null)
}


export function toggleTheme(trigger) {
  return setTheme((current.value || load()) === 'dark' ? 'light' : 'dark', trigger || null)
}

export function useTheme() {
  return { theme: current, isTransitioning }
}


export function getThemeVar() {
  return current.value || load()
}

export default { initTheme, setTheme, toggleTheme, useTheme, THEME_KEY, DEFAULT_THEME }
