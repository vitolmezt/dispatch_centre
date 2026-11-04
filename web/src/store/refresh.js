import { ref } from 'vue'

/**
 * 全局事件总线：数据发生了动作（触发/中止等）后调用 bump()，
 * 各列表页 watch refreshTick 即可刷新一次，避免重复轮询。
 */
const refreshTick = ref(0)

export function bump() {
  refreshTick.value++
}

export { refreshTick }
export default { refreshTick, bump }