import { reactive } from 'vue'

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