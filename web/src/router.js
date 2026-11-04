import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/jobs' },
  { path: '/jobs', component: () => import('./views/MenuBoard.vue'), meta: { title: '指令列表' } },
  { path: '/running', component: () => import('./views/RunningBoard.vue'), meta: { title: '执行中心' } },
  { path: '/history', component: () => import('./views/HistoryBoard.vue'), meta: { title: '执行历史' } },
  { path: '/run/:id', component: () => import('./views/RunDetail.vue'), meta: { title: '执行详情' } },
  { path: '/:pathMatch(.*)*', redirect: '/jobs' },
]

export default createRouter({ history: createWebHistory(), routes })