import { bump } from './refresh'
import { toast } from './toast'
import { getToken, clearAuth } from '../auth'

let controller = null
let reconnectTimer = null
let running = false

const ignored = new Set()

export function ignoreRunIds(ids) {
  for (const id of ids) { if (id) ignored.add(id) }
}

function consumeIgnored(runId) {
  return ignored.delete(runId)
}

function parseEventBlock(block) {
  let dataStr = ''
  for (const rawLine of block.split('\n')) {
    if (!rawLine.startsWith('data:')) continue
    dataStr += rawLine.slice(5).trim() + '\n'
  }
  if (!dataStr.trim()) return null
  try {
    return JSON.parse(dataStr.trim())
  } catch (e) {
    return null
  }
}

async function openStream() {
  if (!running) return
  const token = getToken()
  if (!token) { clearAuth(); return }
  controller = new AbortController()
  try {
    const resp = await fetch('/api/events', {
      signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
      cache: 'no-store',
    })
    if (!resp.ok || !resp.body) {
      if (resp.status === 401) {
        clearAuth()
        document.dispatchEvent(new CustomEvent('sc:unauthorized'))
        return
      }
      throw new Error(`SSE 连接失败: ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    const heartbeat = setInterval(() => { /* keepalive 由后端发送，这里不重复 */ }, 60000)
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          const msg = parseEventBlock(block)
          if (msg && msg.type === 'run') handleRunEvent(msg)
        }
      }
    } finally {
      clearInterval(heartbeat)
    }
  } catch (e) {
    if (e?.name === 'AbortError') return
  }
  scheduleReconnect()
}

function handleRunEvent(d) {
  bump()
  if (d.event === 'finished') {
    if (consumeIgnored(d.run_id)) return
    const name = d.job_name || ''
    const st = d.status
    if (st === 'SUCCEEDED') toast.success(`执行完成「${name}」`)
    else if (st === 'KILLED') toast.info(`已中止「${name}」`)
    else if (st === 'TIMEDOUT') toast.error(`执行超时「${name}」`)
    else toast.error(`执行失败「${name}」`)
  }
}

function scheduleReconnect() {
  if (!running) return
  clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => { openStream() }, 3000)
}

export function connectRealtime() {
  if (running) return
  running = true
  openStream()
}

export function disconnectRealtime() {
  running = false
  clearTimeout(reconnectTimer)
  if (controller) { controller.abort(); controller = null }
}

export default { connectRealtime, disconnectRealtime, ignoreRunIds }