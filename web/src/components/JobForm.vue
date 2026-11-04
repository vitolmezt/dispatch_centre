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
  <form class="jobform" @submit.prevent="onSubmit">
    <!-- 基础字段 -->
    <div class="f-row">
      <label>任务名 <b>*</b></label>
      <input v-model="form.name" required placeholder="如：数据降频脚本" />
    </div>
    <div class="f-row">
      <label>任务类型 <b>*</b></label>
      <select v-model="form.type" required>
        <option v-for="o in typeOptions" :key="o.v" :value="o.v">{{ o.l }}</option>
      </select>
    </div>
    <div class="f-row">
      <label>说明</label>
      <input v-model="form.description" placeholder="一句话说明这个任务做什么" />
    </div>

    <!-- 类型专属参数（动态渲染，来自后端 schema） -->
    <div class="f-head">执行参数</div>
    <template v-for="f in currentSchema" :key="f.key">
      <div class="f-row">
        <label>{{ f.label }} <b v-if="f.required">*</b></label>
        <!-- 下拉 -->
        <select v-if="f.type === 'select'"
                v-model="form.params[f.key]">
          <option value=""></option>
          <option v-for="op in f.options" :key="op" :value="op">{{ op }}</option>
        </select>
        <!-- 开关 -->
        <span v-else-if="f.type === 'bool'" class="f-bool">
          <label class="switch">
            <input type="checkbox" v-model="form.params[f.key]" />
            <span class="slider"></span>
          </label>
          <span class="bool-text">{{ form.params[f.key] ? '是' : '否' }}</span>
        </span>
        <!-- 数组（args，空格分隔） -->
        <span v-else-if="f.type === 'args'" class="f-args">
          <input v-model="argsText[f.key]" :placeholder="f.label + '（空格分隔，可选）'"
                 @change="applyArgs(f.key)" />
        </span>
        <!-- 文本 -->
        <input v-else type="text" v-model="form.params[f.key]" :placeholder="f.label" />
      </div>
    </template>

    <div class="f-head">调度配置</div>
    <div class="f-row">
      <label>定时(Cron)</label>
      <input v-model="form.schedule" placeholder="如 */5 * * * *（留空仅手动触发）" />
    </div>
    <div class="f-row">
      <label>超时(秒)</label>
      <input type="number" min="0" v-model.number="form.timeout_s" placeholder="0=不限" />
    </div>
    <div class="f-row">
      <label>启用</label>
      <span class="f-bool">
        <label class="switch">
          <input type="checkbox" v-model="form.enabled" />
          <span class="slider"></span>
        </label>
        <span class="bool-text">{{ form.enabled ? '已启用' : '已停用' }}</span>
      </span>
    </div>

    <div class="f-actions">
      <button type="button" class="ghost" @click="onCancel">取消</button>
      <button type="submit" class="primary">保存</button>
    </div>
  </form>
</template>

<style scoped>
.jobform { display: flex; flex-direction: column; gap: 12px; }
.f-row { display: flex; flex-direction: column; gap: 5px; }
.f-row label { font-size: 13px; color: var(--muted); }
.f-row label b { color: var(--danger); }
.f-row input, .f-row select {
  background: var(--panel-2); color: var(--text);
  border: 1px solid var(--border); border-radius: 7px;
  padding: 8px 10px; font-size: 13px; width: 100%;
}
.f-head {
  margin-top: 6px; font-size: 12px; color: var(--muted);
  font-weight: 600; text-transform: uppercase; letter-spacing: .5px;
  border-bottom: 1px solid var(--border); padding-bottom: 4px;
}
.f-bool { display: flex; align-items: center; gap: 10px; }
.f-bool .bool-text { font-size: 13px; }
.f-args input { width: 100%; }
.switch { position: relative; display: inline-block; width: 40px; height: 22px; flex: none; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute; cursor: pointer; inset: 0;
  background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 22px; transition: .2s;
}
.slider::before {
  content: ''; position: absolute; height: 16px; width: 16px; left: 3px; top: 2px;
  background: var(--muted); border-radius: 50%; transition: .2s;
}
.switch input:checked + .slider { background: var(--primary); border-color: var(--primary); }
.switch input:checked + .slider::before { transform: translateX(17px); background: #fff; }
.f-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.f-actions button { padding: 8px 18px; font-size: 13px; }
button.ghost { background: transparent; border: 1px solid var(--border); color: var(--muted); }
</style>