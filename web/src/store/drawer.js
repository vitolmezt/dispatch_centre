import { reactive } from 'vue'

/**
 * 全局共享的抽屉状态。
 * 支持两类抽屉：
 *  - type: 'job' —— 选中一个任务名（任务列表 / 详情）
 *  - type: 'run' —— 选中一条执行记录 id（执行历史详情）
 * 任何页面调用 openDrawer(type, value) 都会从右侧滑出对应抽屉。
 */
export const drawerState = reactive({ type: 'job', selected: '' })

export function openJob(name) {
  drawerState.type = 'job'
  drawerState.selected = name
}

export function openRun(runId) {
  drawerState.type = 'run'
  drawerState.selected = runId
}

export function closeDrawer() {
  drawerState.type = 'job'
  drawerState.selected = ''
}

export function closeJob() {
  drawerState.selected = ''
}

export default { drawerState, openJob, openRun, closeJob, closeDrawer }