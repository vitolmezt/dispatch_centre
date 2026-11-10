<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { dict, cronToHuman } from '../constants'
import { pickPath } from '../api'
import { toast } from '../store/toast'

const PATH_FIELDS = {
  path: 'file',
  workdir: 'dir',
  source: 'dir',
  target: 'dir',
}
const props = defineProps({
  schemas: { type: Object, default: () => ({}) },
  initial: { type: Object, default: null },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['submit', 'cancel'])

const labelW = '88px'

const SCHEDULE_PRESETS = [
  { value: '', label: '不自动（仅手动触发）' },
  { value: '*/1 * * * *', label: '每 1 分钟' },
  { value: '*/5 * * * *', label: '每 5 分钟' },
  { value: '*/10 * * * *', label: '每 10 分钟' },
  { value: '0 * * * *', label: '每小时整点' },
  { value: '0 0 * * *', label: '每天 0:00' },
  { value: '0 */2 * * *', label: '每 2 小时整点' },
  { value: '0 9 * * 1-5', label: '工作日每日 9:00' },
  { value: '0 0 * * 1', label: '每周一 0:00' },
]

const typeOptions = computed(() => {
  const g = (props.schemas?.types || dict.groups?.JOB_TYPE || {})
  return Object.entries(g).map(([v, l]) => ({ v, l }))
})

const form = reactive({
  name: '',
  type: 'COMMAND',
  description: '',
  schedule: '',
  timeout_s: 0,
  enabled: true,
  params: {},
})

if (props.initial) {
  form.name = props.initial.name || ''
  form.type = props.initial.type || 'COMMAND'
  form.description = props.initial.description || ''
  form.schedule = props.initial.schedule || ''
  form.timeout_s = props.initial.timeout_s || 0
  form.enabled = props.initial.enabled !== false
  form.params = JSON.parse(JSON.stringify(props.initial.params || {}))
}

const currentSchema = computed(() => props.schemas?.schemas?.[form.type]?.fields || [])

const scheduleDesc = computed(() => {
  if (!form.schedule) return ''
  return cronToHuman(form.schedule) || ''
})

function queryCronSuggestions(queryString, cb) {
  const q = (queryString || '').trim().toLowerCase()
  let list = SCHEDULE_PRESETS
    .filter(p => p.value)
    .map(p => ({ value: p.value, label: p.label, cron: p.value }))
  if (q) {
    list = list.filter(i => i.cron.includes(q) || i.label.includes(q))
  }
  if (q && !list.some(i => i.cron === q)) {
    list.push({ value: q, label: `使用：${q}`, cron: q })
  } else if (!q) {
  }
  cb(list)
}

const actingKey = ref(null)
const isPathField = (key) => Object.prototype.hasOwnProperty.call(PATH_FIELDS, key)
async function openPicker(key) {
  if (actingKey.value) return
  actingKey.value = key
  try {
    const fieldLabel = currentSchema.value.find(f => f.key === key)?.label || key
    const res = await pickPath(PATH_FIELDS[key], {
      title: `选择${fieldLabel}`,
      initial: form.params[key] || '',
    })
    if (res.path) form.params[key] = res.path
  } catch (e) {
    const detail = e?.response?.data?.detail || e.message
    toast.error('选择路径失败: ' + detail)
  } finally {
    actingKey.value = null
  }
}

const argsText = reactive({})
function applyArgs(key) {
  const t = (argsText[key] || '').trim()
  if (t) form.params[key] = t.split(/\s+/)
  else delete form.params[key]
}
if (props.initial) {
  for (const f of (props.schemas?.schemas?.[props.initial.type]?.fields || [])) {
    if (f.type === 'args' && Array.isArray(props.initial.params?.[f.key])) {
      argsText[f.key] = props.initial.params[f.key].join(' ')
    }
  }
}

const prevType = ref(form.type)
watch(() => form.type, (nv) => {
  if (nv === prevType.value) return
  prevType.value = nv
  const keep = new Set(currentSchema.value.map(f => f.key))
  for (const k of Object.keys(form.params)) {
    if (!keep.has(k)) delete form.params[k]
  }
}, { flush: 'post' })

function onSubmit() {
  emit('submit', {
    name: form.name.trim(),
    type: form.type,
    description: form.description,
    schedule: form.schedule || null,
    timeout_s: Number(form.timeout_s) || 0,
    enabled: form.enabled,
    params: JSON.parse(JSON.stringify(form.params)),
  })
}

function onCancel() {
  emit('cancel')
}
</script>

<template>
  <el-form class="jobform" :label-width="labelW" label-position="left" @submit.prevent>
    <div class="fn-fhead">基本信息</div>
    <div class="form-card">
      <el-row :gutter="16">
        <el-col :xs="24" :md="12" class="inline-col">
          <el-form-item label="任务名" required>
            <el-input v-model="form.name" required :disabled="readonly" placeholder="如：数据降频脚本" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :md="12" class="inline-col">
          <el-form-item label="任务类型" required>
            <el-select v-model="form.type" style="width:100%" :disabled="readonly">
              <el-option v-for="o in typeOptions" :key="o.v" :value="o.v" :label="o.l" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="说明">
            <el-input v-model="form.description" :disabled="readonly" placeholder="一句话说明这个任务做什么" />
          </el-form-item>
        </el-col>
      </el-row>
    </div>

    <div class="fn-fhead">执行参数</div>
    <div class="form-card">
      <el-row :gutter="16">
        <el-col v-for="f in currentSchema" :key="f.key" :span="24">
          <el-form-item v-if="f.type === 'bool'" class="bool-inline" :label="f.label" :required="f.required">
            <el-switch v-model="form.params[f.key]" :disabled="readonly"
                       :active-text="form.params[f.key] ? '是' : '否'"
                       style="--el-switch-on-color: var(--primary)" />
          </el-form-item>
          <el-form-item v-else :label="f.label" :required="f.required">
            <el-select v-if="f.type === 'select'" v-model="form.params[f.key]" clearable
                       :disabled="readonly" style="width:100%">
              <el-option v-for="op in f.options" :key="op" :value="op" :label="op" />
            </el-select>
            <el-input v-else-if="f.type === 'args'" v-model="argsText[f.key]" :disabled="readonly"
                      :placeholder="f.label + '（空格分隔，可选）'" @change="applyArgs(f.key)" />
            <template v-else-if="isPathField(f.key)">
              <div class="path-input">
                <el-input v-model="form.params[f.key]" :disabled="readonly" :placeholder="f.label" />
                <el-button v-if="!readonly" class="path-btn" plain
                           :loading="actingKey === f.key"
                           :title="'选择路径（由后端桌面弹出系统选择框）'"
                           @click="openPicker(f.key)">选择…</el-button>
              </div>
            </template>
            <!-- 文本 -->
            <el-input v-else v-model="form.params[f.key]" :disabled="readonly" :placeholder="f.label" />
          </el-form-item>
        </el-col>
      </el-row>
    </div>

    <div class="fn-fhead">调度配置</div>
    <div class="form-card">
      <el-row :gutter="16">
        <el-col :span="24" class="inline-col">
          <el-form-item label="定时">
            <div class="cron-line">
              <el-autocomplete v-model="form.schedule"
                               :fetch-suggestions="queryCronSuggestions"
                               :trigger-on-focus="true"
                               clearable
                               :disabled="readonly"
                               placeholder="点击选择或输入，如 */5 * * * *">
                <template #default="{ item }">
                  <div class="ac-item">
                    <span>{{ item.label }}</span>
                    <span v-if="item.cron" class="cron-monospace">{{ item.cron }}</span>
                  </div>
                </template>
              </el-autocomplete>
              <div v-if="scheduleDesc" class="schedule-desc">{{ scheduleDesc }}</div>
            </div>
          </el-form-item>
        </el-col>
        <el-col :span="24" class="inline-col">
          <el-form-item label="超时(秒)">
            <el-input-number v-model.number="form.timeout_s" :min="0" :disabled="readonly" class="timeout-input"
                             placeholder="0=不限" />
          </el-form-item>
        </el-col>
      </el-row>
    </div>

    <div class="form-card switch-card">
      <el-form-item class="bool-inline" label="启用">
        <el-switch v-model="form.enabled" :disabled="readonly"
                   :active-text="form.enabled ? '已启用' : '已停用'"
                   style="--el-switch-on-color: var(--primary)" />
      </el-form-item>
    </div>

    <el-row v-if="!readonly" justify="center" class="fn-actions">
      <div class="fn-action-btns">
        <el-button @click="onCancel">取消</el-button>
        <el-button type="primary" @click="onSubmit">保存</el-button>
      </div>
    </el-row>
  </el-form>
</template>

<style scoped>
.jobform { display: block; }
.jobform {
  --el-font-size-base: 13px;
  --el-component-size: 28px;
  --el-font-size-small: 12px;
}

.jobform :deep(.el-form-item__label) {
  width: 88px; flex: 0 0 88px; text-align: left; line-height: 28px;
  box-sizing: border-box; padding: 0 0 0 8px; word-break: normal; position: relative;
}
.jobform :deep(.el-form-item.is-required .el-form-item__label::before) {
  content: "*" !important;
  position: absolute !important;
  left: 0 !important; top: 0 !important;
  width: 12px !important;
  margin-right: 0 !important;
  line-height: 28px !important;
}
.jobform :deep(.el-form-item__label::before) {
  content: none !important;
}

.jobform .inline-col :deep(.el-form-item__label),
.jobform .timeout-field :deep(.el-form-item__label),
.jobform .bool-inline :deep(.el-form-item__label) {
  width: auto; flex: none; text-align: left; line-height: normal;
}

.jobform .inline-col :deep(.el-form-item__content),
.jobform .timeout-field :deep(.el-form-item__content),
.jobform .bool-inline :deep(.el-form-item__content) {
  margin-left: 0 !important; padding-left: 0 !important;
}
.jobform .inline-col :deep(.el-form-item__label) {
  padding: 0 0 0 8px; line-height: 28px;
}
.jobform .timeout-field :deep(.el-form-item__label) {
  width: 88px; flex: 0 0 88px; padding-right: 4px; line-height: 28px;
}

.jobform .bool-inline :deep(.el-form-item__label) {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  padding-left: 8px; line-height: 28px;
  max-width: calc(100% - 90px); flex: 0 1 auto;
}
.jobform .bool-inline :deep(.el-form-item__content) {
  display: flex; align-items: center;
}

.fn-fhead {
  margin: 2px 0 6px; font-size: 12px; color: var(--muted);
  font-weight: 600; letter-spacing: .5px;
  border-left: 3px solid var(--primary);
  padding-left: 8px; line-height: 1.2;
}

.form-card {
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border, #3a3f46);
  border-radius: 8px;
  padding: 12px 12px 2px;
  margin-bottom: 10px;
}
.form-card .el-form-item { margin-bottom: 10px; }

.fn-actions { margin-top: 4px; }
.fn-actions :deep(.el-form-item) { margin-bottom: 0; }
.fn-action-btns { display: inline-flex; gap: 12px; align-items: center; }

.path-input { display: flex; gap: 8px; width: 100%; align-items: center; }
.path-input .el-input { flex: 1 1 auto; min-width: 0; }
.path-input .el-input .el-input__wrapper { height: 28px; }
.path-input .el-button { flex: 0 0 auto; }
.path-btn {
  height: 28px; line-height: 1; padding: 0 14px; white-space: nowrap;
  display: inline-flex; align-items: center; justify-content: center;
}

.cron-line { display: flex; align-items: center; gap: 16px; min-width: 0; }
.cron-line .el-autocomplete,
.cron-line .el-autocomplete .el-input,
.cron-line .el-autocomplete .el-input__wrapper { width: 340px !important; max-width: 100%; }
.schedule-desc { flex: 0 0 auto; }

.ac-item { display: flex; align-items: center; justify-content: space-between; gap: 24px; width: 100%; }
.cron-monospace {
  font-family: Consolas, monospace;
  font-size: 12px; color: var(--muted, #888);
}

.schedule-desc {
  color: var(--primary, #4a9eff);
  font-size: 13px; white-space: nowrap; font-weight: 600;
}

.timeout-input { width: 340px !important; max-width: 100%; min-width: 120px; box-sizing: border-box; }
.timeout-input .el-input__wrapper { box-sizing: border-box; }
.timeout-input .el-input-number__increase,
.timeout-input .el-input-number__decrease { padding: 0 6px; width: auto; }
.timeout-input .el-input__inner { text-align: left; padding-right: 44px; }
</style>