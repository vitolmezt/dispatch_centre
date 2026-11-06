import { bump } from './refresh'
import { toast } from './toast'

/**
 * 实时事件订阅（SSE）。
 * 后端在任何任务 开始 / 结束 / 中止 时推送事件：
 *  - 结束(finished)：在屏幕中心上方弹动作结果提示（成功/失败/超时/中止）。
 * 页面一加载即建立连接，无需手动操作、也无需轮询。
 *
 * ignoreRunIds()：用于"一次性停止整组"等批量操作——那些成员 run 的完成通知
 * 由调用方统一给一条汇总提示，避免一堆子服务各自弹"已中止"。
 */
let es = null

// 已申请忽略完成通知的 run_id 集合（如整组停止时的各子服务）。
// 命中则只 bump 刷新、不弹 toast；收到其 finished 后自动移除以防误伤后续同名 run。
const ignored = new Set()

export function ignoreRunIds(ids) {
  for (const id of ids) { if (id) ignored.add(id) }
}

function consumeIgnored(runId) {
  return ignored.delete(runId)
}

export function connectRealtime() {
  if (es) return es
  try {
    es = new EventSource('/api/events')
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data)
        if (d?.type === 'run') {
          bump()
          if (d.event === 'finished') {
            // 整组停止等场景：这些 run 由调用方统一提示，这里不再逐个弹
            if (consumeIgnored(d.run_id)) return
            const name = d.job_name || ''
            const st = d.status
            if (st === 'SUCCEEDED') toast.success(`执行完成「${name}」`)
            else if (st === 'KILLED') toast.info(`已中止「${name}」`)
            else if (st === 'TIMEDOUT') toast.error(`执行超时「${name}」`)
            else toast.error(`执行失败「${name}」`)
          }
        }
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