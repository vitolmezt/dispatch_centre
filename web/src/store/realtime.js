import { bump } from './refresh'
import { toast } from './toast'
import { getToken, clearAuth } from '../auth'

/**
 * 实时事件订阅（SSE）—— 阶段1 改用 fetch + ReadableStream 以携带 Authorization 头
 * （原生 EventSource 无法自定义请求头，token 又不允许进 URL）。
 * 后端在任何任务 开始 / 结束 / 中止 时推送事件：
 *  - 结束(finished)：在屏幕中心上方弹动作结果提示（成功/失败/超时/中止）。
 * 页面一加载即建立连接，无需手动操作、也无需轮询。
 *
 * ignoreRunIds()：用于"一次性停止整组"等批量操作——那些成员 run 的完成通知
 * 由调用方统一给一条汇总提示，避免一堆子服务各自弹"已中止"。
 *
 * 说明：本文件不再使用原生 EventSource，而是 fetch 长连接 + 手动解析 SSE 事件，
 * 这样可在请求头中带 Authorization: Bearer，并实现断线重连、异常处理与连接取消。
 */

let controller = null        // AbortController 用于取消连接
let reconnectTimer = null
let running = false

// 已申请忽略完成通知的 run_id 集合。命中则只 bump 刷新、不弹 toast；收到后自动移除。
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
    // 每 20 秒发送一行注释心跳，缓解 QUIC/proxy 空闲断开
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
    // 主动取消不算错误
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
  reconnectTimer = setTimeout(() => { openStream() }, 3000) // EventSource 默认 ~3s 重连
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