import { ref } from 'vue'

/**
 * 前端认证状态（阶段1）。
 * - Token 仅存 sessionStorage/内存，不写源码、不写日志、不进 URL。
 * - axios 拦截器注入 Authorization: Bearer；SSE(fetch) 也注入。
 * - 提供登录/登出/token 失效处理。
 */
const TOKEN_KEY = 'sc_session_token'

// 内存中的 token（页面刷新前优先），sessionStorage 作跨刷新保留
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

/** 退出登录：清空本地 token（调用方需再请求 /api/logout 吊销会话） */
export function clearAuth() {
  current.value = ''
  sessionStorage.removeItem(TOKEN_KEY)
}

export default { getToken, setToken, clearAuth, isAuthenticated }