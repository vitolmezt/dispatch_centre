import { ref } from 'vue'


const TOKEN_KEY = 'sc_session_token'

const current = ref('')

export function getToken() {
  if (current.value) return current.value
  const saved = sessionStorage.getItem(TOKEN_KEY)
  if (saved) current.value = saved
  return current.value
}

export function setToken(token) {
  current.value = token || ''
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export function isAuthenticated() {
  return !!getToken()
}

export function clearAuth() {
  current.value = ''
  sessionStorage.removeItem(TOKEN_KEY)
}

export default { getToken, setToken, clearAuth, isAuthenticated }