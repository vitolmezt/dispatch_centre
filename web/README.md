# 调度中心 WEB 前端 —— 全局常量映射说明

前端的常量字典来自后端 `GET /api/constants`，启动时（App.vue `onMounted`）自动拉取，
存进 `src/constants.js` 的 `dict.groups`。所有枚举文案统一从这里翻译，遇到整个系统
不再重复硬编码中文。

## 用法示例

```js
import { statusLabel, jobTypeLabel, triggerLabel, t } from '@/constants'

statusLabel('FAILED')   // '失败'
jobTypeLabel('SCRIPT')  // '脚本'
triggerLabel('CRON')    // '定时触发'
t('STATUS', 'PENDING')  // '待开始'（通用）
```

## 想加新的常量分组？

只改后端 `server/app/constants.py`：在 `ALL_GROUPS` 里新增一个 dict 即可。
前端重启后自动拿到新分组，无需改前端代码。