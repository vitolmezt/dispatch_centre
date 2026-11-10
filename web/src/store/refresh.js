import { ref } from 'vue'

const refreshTick = ref(0)

export function bump() {
  refreshTick.value++
}

export { refreshTick }
export default { refreshTick, bump }