<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { dict } from '../constants'

/**
 * 指令配置表单：类型驱动。
 * 选了 JOB_TYPE 后，按后端 /api/job-schema 下发的 params 字段结构，
 * 动态渲染出每个参数对应的输入框/下拉/开关，完全不硬编码参数。
 * 也负责基础字段：名称、描述、定时、超时、启用。
 */
const props = defineProps({
  schemas: { type: Object, default: () => ({}) },  // {types, schemas:{COMMAND:{fields:[]},...}}
  initial: { type: Object, default: null },         // 编辑时的任务初始值
})
const emit = defineEmits(['submit', 'cancel'])

const typeOptions = computed(() => {
  const g = (props.schemas?.types || dict.groups?.JOB_TYPE || {})
  return Object.entries(g).map(([v, l]) => ({ v, l }))
})

// 表单状态
const form = reactive({
  name: '',
  type: 'COMMAND',
  description: '',
  schedule: '',
  timeout_s: 0,
  enabled: true,
  params: {},
})

// 编辑时回填
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

// args 数组字段用「空格分隔」输入框编辑；change 时同步回 form.params
const argsText = reactive({})
function applyArgs(key) {
  const t = (argsText[key] || '').trim()
  if (t) form.params[key] = t.split(/\s+/)
  else delete form.params[key]
}
// 编辑时预填 args 文本
if (props.initial) {
  for (const f of (props.schemas?.schemas?.[props.initial.type]?.fields || [])) {
    if (f.type === 'args' && Array.isArray(props.initial.params?.[f.key])) {
      argsText[f.key] = props.initial.params[f.key].join(' ')
    }
  }
}

// 切类型时，尽量保留共名字段，其余重置为默认
// （避免残留上一个类型的参数）
const prevType = ref(form.type)
watch(() => form.type, (nv) => {
  if (nv === prevType.value) return
  prevType.value = nv
  const keep = new Set(currentSchema.value.map(f => f.key))
  for (const k of Object.keys(form.params)) {
    if (!keep.has(k)) delete form.params[k]
  }
}, { flush: 'post' })

// 提交：整理成后端接受的 payload，name 允许「新增/普通输入」。
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
  <el-form class="jobform" label-position="top" @submit.prevent>
    <!-- 基础字段 -->
    <el-row :gutter="16">
      <el-col :xs="24" :md="12">
        <el-form-item label="任务名" required>
          <el-input v-model="form.name" required placeholder="如：数据降频脚本" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-form-item label="任务类型" required>
          <el-select v-model="form.type" style="width:100%">
            <el-option v-for="o in typeOptions" :key="o.v" :value="o.v" :label="o.l" />
          </el-select>
        </el-form-item>
      </el-col>
      <el-col :span="24">
        <el-form-item label="说明">
          <el-input v-model="form.description" placeholder="一句话说明这个任务做什么" />
        </el-form-item>
      </el-col>
    </el-row>

    <!-- 类型专属参数（动态渲染，来自后端 schema） -->
    <div class="fn-fhead">执行参数</div>
    <el-row :gutter="16">
      <el-col v-for="f in currentSchema" :key="f.key" :xs="24" :md="12">
        <el-form-item :label="f.label" :required="f.required">
          <!-- 下拉 -->
          <el-select v-if="f.type === 'select'" v-model="form.params[f.key]" clearable style="width:100%">
            <el-option v-for="op in f.options" :key="op" :value="op" :label="op" />
          </el-select>
          <!-- 开关 -->
          <el-switch v-else-if="f.type === 'bool'"
                     v-model="form.params[f.key]"
                     :active-text="form.params[f.key] ? '是' : '否'"
                     style="--el-switch-on-color: var(--primary)" />
          <!-- 数组（args，空格分隔） -->
          <el-input v-else-if="f.type === 'args'" v-model="argsText[f.key]"
                    :placeholder="f.label + '（空格分隔，可选）'" @change="applyArgs(f.key)" />
          <!-- 文本 -->
          <el-input v-else v-model="form.params[f.key]" :placeholder="f.label" />
        </el-form-item>
      </el-col>
    </el-row>

    <div class="fn-fhead">调度配置</div>
    <el-row :gutter="16">
      <el-col :xs="24" :md="12">
        <el-form-item label="定时(Cron)">
          <el-input v-model="form.schedule" placeholder="如 */5 * * * *（留空仅手动触发）" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-form-item label="超时(秒)">
          <el-input-number v-model.number="form.timeout_s" :min="0" style="width:100%"
                           placeholder="0=不限" />
        </el-form-item>
      </el-col>
      <el-col :span="24">
        <el-form-item label="启用">
          <el-switch v-model="form.enabled"
                     :active-text="form.enabled ? '已启用' : '已停用'"
                     style="--el-switch-on-color: var(--primary)" />
        </el-form-item>
      </el-col>
    </el-row>

    <el-row justify="end" class="fn-actions">
      <el-form-item>
        <el-button @click="onCancel">取消</el-button>
        <el-button type="primary" @click="onSubmit">保存</el-button>
      </el-form-item>
    </el-row>
  </el-form>
</template>

<style scoped>
.jobform { display: block; }
.fn-fhead {
  margin: 6px 0 12px; font-size: 12px; color: var(--muted);
  font-weight: 600; text-transform: uppercase; letter-spacing: .5px;
  border-bottom: 1px solid var(--border); padding-bottom: 4px;
}
.fn-actions { margin-top: 8px; }
</style>