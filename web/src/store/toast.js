import { ElMessage } from 'element-plus'

export const toast = {
  success: (msg, opt) => ElMessage.success(msg, opt),
  error: (msg, opt) => ElMessage.error(msg, opt),
  info: (msg, opt) => ElMessage.info(msg, opt),
}

export default toast