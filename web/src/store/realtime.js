import { bump } from './refresh'

/**
 * 实时事件订阅（SSE）。
 * 后端在任何任务 开始 / 结束 / 中止 时推送事件；
 * 前端收到后触发一次全局 bump，所有已挂载看板(response 的 refreshTick)各自刷新一次。
 * 页面一加载即建立连接，无需手动操作、也无需轮询。
 */
let es = null

export function connectRealtime() {
  if (es) return es
  try {
    es = new EventSource('/api/events')
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data)
        if (d?.type === 'run') bump()
      } catch (e) { /* ignore */ }
    }
    // 断线自动重连（EventSource 默认重连）
    es.onerror = () => { /* EventSource 自动重连 */ }
  } catch (e) {
    es = null
  }
  return es
}

export function disconnectRealtime() {
  if (es) { es.close(); es = null }
}

export default { connectRealtime, disconnectRealtime }