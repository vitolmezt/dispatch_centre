<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { dict } from '../constants'
import { pickPath } from '../api'
import { toast } from '../store/toast'

/**
 * 指令配置表单：类型驱动。
 * 选了 JOB_TYPE 后，按后端 /api/job-schema 下发的 params 字段结构，
 * 动态渲染出每个参数对应的输入框/下拉/开关，完全不硬编码参数。
 * 也负责基础字段：名称、描述、定时、超时、启用。
 * 其中「路径类」字段（工作目录 / 脚本路径 / 程序路径 / 源目标等）附带一个
 * 「选择」按钮：由后端弹出系统原生文件/文件夹选择框，选中的真实绝对
 * 路径直接回填输入框（浏览器拿不到真实路径，故走后端）。
 */

// 哪些字段是路径类、以及期望选文件还是目录
const PATH_FIELDS = {
  path: 'file',        // 脚本/程序路径：选文件
  workdir: 'dir',      // 工作目录：选文件夹
  source: 'dir',       // FILEOP 源路径：目录或文件（选目录，常为文件夹）
  target: 'dir',       // FILEOP 目标路径
}
const props = defineProps({
  schemas: { type: Object, default: () => ({}) },  // {types, schemas:{COMMAND:{fields:[]},...}}
  initial: { type: Object, default: null },         // 编辑时的任务初始值
})
const emit = defineEmits(['submit', 'cancel'])

// 统一标签固定宽度（左标签），保证所有输入框左边缘对齐
const labelW = '110px'

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

// 路径选择：后端点系统原生对话框；actingKey 记录正在请求的字段名（按钮 loading）
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
  <el-form class="jobform" :label-width="labelW" label-position="left" @submit.prevent>
    <!-- ① 基本信息 -->
    <div class="fn-fhead">基本信息</div>
    <div class="form-card">
      <el-row :gutter="16">
        <el-col :xs="24" :md="12" class="inline-col">
          <el-form-item label="任务名" required>
            <el-input v-model="form.name" required placeholder="如：数据降频脚本" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :md="12" class="inline-col">
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
    </div>

    <!-- ② 执行参数（动态渲染，来自后端 schema；每个字段独占一行避免挤压） -->
    <div class="fn-fhead">执行参数</div>
    <div class="form-card">
      <el-row :gutter="16">
        <el-col v-for="f in currentSchema" :key="f.key" :span="24">
          <!-- 开关/布尔：标签 auto 宽度 + 单行 nowrap，开关与标签同行（EP 原生 flex 内部布局） -->
          <el-form-item v-if="f.type === 'bool'" class="bool-inline" :label="f.label" :required="f.required">
            <el-switch v-model="form.params[f.key]"
                       :active-text="form.params[f.key] ? '是' : '否'"
                       style="--el-switch-on-color: var(--primary)" />
          </el-form-item>
          <!-- 其余字段：统一左标签对齐 -->
          <el-form-item v-else :label="f.label" :required="f.required">
            <!-- 下拉 -->
            <el-select v-if="f.type === 'select'" v-model="form.params[f.key]" clearable style="width:100%">
              <el-option v-for="op in f.options" :key="op" :value="op" :label="op" />
            </el-select>
            <!-- 数组（args，空格分隔） -->
            <el-input v-else-if="f.type === 'args'" v-model="argsText[f.key]"
                      :placeholder="f.label + '（空格分隔，可选）'" @change="applyArgs(f.key)" />
            <!-- 路径类：输入框 + 「选择」按钮（后端点系统原生对话框，回填真实路径） -->
            <template v-else-if="isPathField(f.key)">
              <div class="path-input">
                <el-input v-model="form.params[f.key]" :placeholder="f.label" />
                <el-button class="path-btn" plain :loading="actingKey === f.key"
                           @click="openPicker(f.key)">选择…</el-button>
              </div>
            </template>
            <!-- 文本 -->
            <el-input v-else v-model="form.params[f.key]" :placeholder="f.label" />
          </el-form-item>
        </el-col>
      </el-row>
    </div>

    <!-- ③ 调度配置（仅定时/超时；启用为任务总开关，放在底部） -->
    <div class="fn-fhead">调度配置</div>
    <div class="form-card">
      <div class="schedule-row">
        <!-- Cron 弹性占满剩余宽度；显式固定 label-width，避开 flex 容器下继承失效 -->
        <div class="field cron-field">
          <el-form-item :label-width="labelW" label="定时(Cron)">
            <el-input v-model="form.schedule" placeholder="如 */5 * * * *（留空仅手动触发）" />
          </el-form-item>
        </div>
        <!-- 超时固定窄列（标签居左，标签用紧凑宽度，保证数字组件不被挤压坍缩） -->
        <div class="field timeout-field">
          <el-form-item label="超时(秒)">
            <el-input-number v-model.number="form.timeout_s" :min="0" class="timeout-input"
                             style="width:100%" placeholder="0=不限" />
          </el-form-item>
        </div>
      </div>
    </div>

    <!-- ④ 任务总开关 -->
    <div class="form-card switch-card">
      <el-form-item class="is-top" label-position="top" label="启用">
        <el-switch v-model="form.enabled"
                   :active-text="form.enabled ? '已启用' : '已停用'"
                   style="--el-switch-on-color: var(--primary)" />
      </el-form-item>
    </div>

    <el-row justify="center" class="fn-actions">
      <el-form-item>
        <el-button @click="onCancel">取消</el-button>
        <el-button type="primary" @click="onSubmit">保存</el-button>
      </el-form-item>
    </el-row>
  </el-form>
</template>

<style scoped>
.jobform { display: block; }

/* 统一标签固定宽度 & 输入框左对齐：作用于所有左标签单列表单项，穿透到内部组件。
   对齐基准 = 中文文字起点。必填红星脱离文档流（absolute）不占对齐宽度；
   只有 required 字段显示红星，非必填不显示。所有标签统一预留左侧 14px 星位槽位，
   故带星与不带星字段的文字起点一致。标签盒子固定宽度、padding-right:0 贴近输入框。 */
.jobform :deep(.el-form-item__label) {
  width: 110px; flex: 0 0 110px; text-align: left; line-height: 32px;
  box-sizing: border-box; padding: 0 0 0 14px; word-break: normal; position: relative;
}
/* 仅必填项显示红星，且脱离文档流（压制 EP 自带样式，非必填不出现） */
.jobform :deep(.el-form-item.is-required .el-form-item__label::before) {
  content: "*" !important;
  position: absolute !important;
  left: 0 !important; top: 0 !important;
  width: 12px !important;
  margin-right: 0 !important;
  line-height: 32px !important;
}
/* 非必填项确保无红星 */
.jobform :deep(.el-form-item__label::before) {
  content: none !important;
}
/* 特殊项豁免定宽标签：
   1) 顶部标签的开关项（启用 → .is-top）
   2) 双列并排的“任务名/任务类型”（.inline-col，空间有限，标签自适应宽度）
   3) 调度配置里的超时字段（.timeout-field，标签居左但列宽有限，用紧凑宽度）
   4) bool 开关项（后台常驻等，标签 auto + 单行，开关与标签同行，不建立根 flex） */
.jobform :deep(.el-form-item.is-top .el-form-item__label),
.jobform .inline-col :deep(.el-form-item__label),
.jobform .timeout-field :deep(.el-form-item__label),
.jobform .bool-inline :deep(.el-form-item__label) {
  width: auto; flex: none; text-align: left; line-height: normal;
}
/* 豁免项的 content 不套用 label-width 偏移，避免错位缩进
   （用 !important 压制 EP 内联的 margin-left） */
.jobform :deep(.el-form-item.is-top .el-form-item__content),
.jobform .inline-col :deep(.el-form-item__content),
.jobform .timeout-field :deep(.el-form-item__content),
.jobform .bool-inline :deep(.el-form-item__content) {
  margin-left: 0 !important; padding-left: 0 !important;
}
.jobform :deep(.el-form-item.is-top .el-form-item__label) {
  padding: 0 0 6px; line-height: 20px; white-space: nowrap;
}
.jobform .inline-col :deep(.el-form-item__label) {
  padding: 0 0 0 14px; line-height: 32px;
}
.jobform .timeout-field :deep(.el-form-item__label) {
  width: 96px; flex: 0 0 96px; padding-right: 4px; line-height: 32px;
}
/* bool 开关项：标签 auto 宽度 + 单行不换行（超长省略），开关与标签同行 */
.jobform .bool-inline :deep(.el-form-item__label) {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  padding-left: 14px; line-height: 32px;
  max-width: calc(100% - 90px); flex: 0 1 auto;
}
.jobform .bool-inline :deep(.el-form-item__content) {
  display: flex; align-items: center;
}

/* 模块标题 */
.fn-fhead {
  margin: 4px 0 10px; font-size: 13px; color: var(--muted);
  font-weight: 600; letter-spacing: .5px;
  border-left: 3px solid var(--primary);
  padding-left: 8px; line-height: 1.2;
}

/* 模块卡片：分隔各分区，内边距统一，底部留间距 */
.form-card {
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border, #3a3f46);
  border-radius: 8px;
  padding: 16px 16px 4px;
  margin-bottom: 18px;
}
.form-card .el-form-item { margin-bottom: 14px; }

/* 行尾操作按钮（取消/保存） */
.fn-actions { margin-top: 4px; }
.fn-actions :deep(.el-form-item) { margin-bottom: 0; }

/* 路径字段：输入框 + 「选择…」按钮，一行对齐，等高等宽避免裁切 */
.path-input { display: flex; gap: 8px; width: 100%; align-items: center; }
.path-input .el-input { flex: 1 1 auto; min-width: 0; }
.path-input .el-input .el-input__wrapper { height: 32px; }
.path-input .el-button { flex: 0 0 auto; }
.path-btn {
  height: 32px; line-height: 1; padding: 0 16px; white-space: nowrap;
  display: inline-flex; align-items: center; justify-content: center;
}

/* 调度配置一行：Cron 弹性占宽，超时固定宽度互不挤占 */
.schedule-row { display: flex; gap: 24px; align-items: flex-end; }
.schedule-row .cron-field { flex: 1 1 auto; min-width: 0; }
.schedule-row .timeout-field { flex: 0 0 240px; min-width: 240px; }
.schedule-row .field { max-width: 100%; }

/* 超时数字组件：约束在区块内不溢出，加减按钮留足内边距防裁切 */
.timeout-input { width: 100% !important; min-width: 120px; box-sizing: border-box; }
.timeout-input .el-input__wrapper { box-sizing: border-box; }
.timeout-input .el-input-number__increase,
.timeout-input .el-input-number__decrease { padding: 0 6px; width: auto; }
.timeout-input .el-input__inner { text-align: left; padding-right: 44px; }

/* 开关卡片（启用/后台常驻）：标签在上，开关在下，两处开关保持统一 */
.switch-card { padding-top: 14px; }
.switch-card .el-form-item { margin-bottom: 6px; }
.el-form-item.is-top .el-form-item__label { text-align: left; padding: 0 0 6px; }
</style>