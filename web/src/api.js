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

export async function pickPath(kind, options = {}) {
  const { data } = await http.post('/fs/pick', { kind, ...options })
  return data
}

export default http