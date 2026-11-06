import axios from 'axios'

/** 后端统一走 /api 前缀，开发时由 vite proxy 转发到 127.0.0.1:8000. */
const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

export async function fetchConstants() {
  const { data } = await http.get('/constants')
  return data
}

export async function fetchJobs(params = {}) {
  const { data } = await http.get('/jobs', { params })
  return data
}

export async function createJob(payload) {
  const { data } = await http.post('/jobs', payload)
  return data
}

export async function updateJob(name, payload) {
  const { data } = await http.put(`/jobs/${encodeURIComponent(name)}`, payload)
  return data
}

export async function deleteJob(name) {
  const { data } = await http.delete(`/jobs/${encodeURIComponent(name)}`)
  return data
}

export async function enableJob(name, enabled) {
  const { data } = await http.post(`/jobs/${encodeURIComponent(name)}/enable`, { enabled })
  return data
}

export async function fetchJobSchema() {
  const { data } = await http.get('/job-schema')
  return data
}

export async function fetchGroups() {
  const { data } = await http.get('/groups')
  return data
}

export async function fetchRunning(params = {}) {
  const { data } = await http.get('/running', { params })
  return data
}

export async function fetchHistory(params = {}) {
  const { data } = await http.get('/history', { params })
  return data
}

export async function fetchRun(runId) {
  const { data } = await http.get(`/runs/${runId}`)
  return data
}

/**
 * 订阅一次执行（run）的实时日志流（SSE）。
 * 通过 fetch 手动解析 text/event-stream，逐行回调；收到 end 或出错时 onEnd()。
 * 用于运行详情页在任务执行中实时追加终端输出。
 */
export function streamRunLog(runId, { onLine, onEnd } = {}) {
  let aborted = false
  const controller = new AbortController()

  const task = (async () => {
    const resp = await fetch(`/api/runs/${encodeURIComponent(runId)}/stream`, {
      signal: controller.signal,
      headers: { 'Accept': 'text/event-stream' },
    })
    if (!resp.ok || !resp.body) { onEnd?.(); return }
    const decoder = new TextDecoder()
    const reader = resp.body.getReader()
    let buf = ''
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        // 按空行切分 SSE 事件块
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          for (const rawLine of chunk.split('\n')) {
            if (!rawLine.startsWith('data:')) continue
            const data = rawLine.slice(5).trim()
            if (!data || data.startsWith(':')) continue
            try {
              const msg = JSON.parse(data)
              if (msg.type === 'log') {
                if (msg.end) { onEnd?.(); return }
                if (msg.line) onLine?.(msg.line)
              }
            } catch (e) { /* skip malformed */ }
          }
        }
      }
    } finally {
      try { reader.releaseLock() } catch (e) { /* ignore */ }
    }
    onEnd?.()
  })()

  return {
    close: () => { if (!aborted) { aborted = true; controller.abort() } },
    promise: task,
  }
}

export async function triggerJob(name) {
  const { data } = await http.post(`/jobs/${name}/trigger`)
  return data
}

export async function abortRun(runId) {
  const { data } = await http.post(`/runs/${runId}/abort`)
  return data
}

export async function stopRun(runId) {
  const { data } = await http.post(`/runs/${runId}/stop`)
  return data
}

export async function fetchServiceGroups() {
  const { data } = await http.get('/service-groups')
  return data
}

export async function createServiceGroup(payload) {
  const { data } = await http.post('/service-groups', payload)
  return data
}

export async function updateServiceGroup(name, payload) {
  const { data } = await http.put(`/service-groups/${encodeURIComponent(name)}`, payload)
  return data
}

export async function deleteServiceGroup(name) {
  const { data } = await http.delete(`/service-groups/${encodeURIComponent(name)}`)
  return data
}

export async function startServiceGroup(name) {
  const { data } = await http.post(`/service-groups/${encodeURIComponent(name)}/start`)
  return data
}

export async function stopServiceGroup(name) {
  const { data } = await http.post(`/service-groups/${encodeURIComponent(name)}/stop`)
  return data
}

export async function fetchServiceGroup(name) {
  const { data } = await http.get(`/service-groups/${encodeURIComponent(name)}`)
  return data
}

export async function pickPath(kind, options = {}) {
  const { data } = await http.post('/fs/pick', { kind, ...options })
  return data
}

export default http