# Scheduler Center 前端统一布局规范

> 生效范围：`web/src` 所有页面与组件。新增页面必须遵循本规范。
> 技术栈定位：**Element Plus（栅格 + 组件）+ 少量项目工具类**，PC 优先、兼顾平板。

---

## 1. 核心技术事项

| 项 | 实现 |
|---|---|
| 组件库 | Element Plus（全量引入，中文 locale，暗色主题） |
| 布局引擎 | `el-row` / `el-col` 24 栅格 |
| 框架骨架 | `el-container` / `el-aside` / `el-header` / `el-main` |
| 列表 | `el-table`（后端排序、自动列宽、空态） |
| 详情 | `el-drawer` 全局右侧抽屉（点击空白关闭） |
| 弹窗/确认 | `el-dialog` / `ElMessageBox`（禁用浏览器原生 confirm/alert） |
| 间距/圆角 | CSS 变量 token（本规范 §3） |
| 主题 | `html.dark` + Element Plus 暗色 + 项目 `--` 变量对齐 |

---

## 2. 全局框架（App.vue，全站唯一）

```
el-container（纵向撑满）
├─ el-aside     左侧导航（220px）
└─ el-container（横向）
   ├─ el-header  顶部栏（页面标题）
   ├─ el-main    内容区（.content，内边距 22px）
   └─ footer     页脚
```

底部挂两个**全局抽屉**：`JobDrawer`（指令详情）、`RunDetailDrawer`（执行详情），由 `store/drawer.js` 统一控制，任何页面调 `openJob(name)` / `openRun(id)` 都能滑出。

---

## 3. 布局 Token（CSS 变量）

统一在 `web/src/style.css` 的 `:root` 定义，禁止在业务代码里裸写魔法数字。

```css
:root {
  /* 间距 */
  --space-xs: 8px;
  --space-sm: 12px;
  --space:  16px;
  --space-lg: 22px;
  --space-xl: 32px;

  /* 圆角 */
  --radius-sm: 6px;
  --radius: 8px;
  --radius-lg: 12px;

  /* 框架尺寸 */
  --aside-w: 220px;
  --aside-w-collapsed: 64px;
  --topbar-h: 56px;

  /* 容器（--container-max 仅参考；.page 现为 100% 宽铺满 el-main） */
  --container-max: 1400px;

  /* 栅格 gap（对应 el-row :gutter 的一半） */
  --grid-gutter: 16px;
}
```

---

## 4. 页面容器规则

- 每个路由页根元素用统一类 `.page`：`width:100%`，占满 `el-main.content`；横向留白由 `.content` 的 `padding:22px` 统一提供，不在 `.page` 上叠加，避免列表双重内边距。
- 页面卡片用 `el-card shadow="never"`（暗色下边框分隔）。
- 竖向留白：区块间 `--space-xl`，区块内 `--space`。

---

## 5. 组件规范

### 列表页（指令列表 / 执行中心 / 执行历史）
结构：**搜索栏卡片 + 表格卡片 + 分页**。

```vue
<div class="page">
  <el-card shadow="never" class="toolbar">
    <el-form inline @submit.prevent>
      <el-form-item label="…">…</el-form-item>
      <!-- 主操作按钮放表单最后一个 item，右对齐同一行 -->
      <el-form-item class="toolbar-btn">
        <el-button type="primary">+ 新增</el-button>
      </el-form-item>
    </el-form>
  </el-card>
  <el-card shadow="never" class="table-card">
    <el-table :data="items" v-loading="loading" stripe @sort-change="onSortChange">
      <el-table-column ... sortable="custom"/>
    </el-table>
    <PagerBar :page="page" :total="total" :page-size="pageSize" @change="…" />
  </el-card>
</div>
```

- **不放「重置」按钮**（已按产品要求移除），搜索条件变化即自动刷新，名称搜索去抖 300ms。
- 表单项与右侧主按钮同处一行：表单整行 `flex; flex-wrap:nowrap`，按钮 item `margin-left:auto` 顶到最右；屏幕不足时工具栏横向滚动（`.toolbar{overflow-x:auto}`）而非换行错位。
- 排序为后端排序：列加 `sortable="custom"`，`@sort-change` 把 `prop/order` 传给后端 `sort/order` 参数。
- 操作列 `fixed="right"`，按钮组 `el-button size="small"`。

### 搜索栏工具条（右对齐按钮）
```css
.toolbar { overflow-x: auto; }
.toolbar :deep(.el-form--inline) { display: flex; align-items: center; flex-wrap: nowrap; gap: var(--space); }
.toolbar :deep(.el-form-item) { margin-bottom: 0; margin-right: 0; flex: 0 0 auto; }
.toolbar :deep(.el-form-item.toolbar-btn) { margin-left: auto; }
```

### 详情抽屉
- `RunDetailDrawer`：`size="80%"`（约 4/5 屏宽），`:close-on-click-modal="true"`（点空白关闭），实时轮询日志（运行中每 2.5s）。
- `JobDrawer`：指令详情（基本信息 + 配置参数）。

### 表单 / 弹窗
- 表单用 `el-form` + `el-row/el-col` 栅格化；`label-position="top"` 可避免窄弹窗内 label 挤压输入框。
- 确认类操作用 `ElMessageBox.confirm`（删除等）。

### 提示 / 反馈
- 统一走 `store/toast.js`（转发 ElMessage）：`toast.success/info/error`。
- 禁用 `alert()` / `window.confirm()`。

---

## 6. 新增页面 Checklist

- [ ] 根元素用 `.page`
- [ ] 列表用 `el-table`；表单用 `el-form` + 栅格
- [ ] 搜索栏主按钮与筛选项同一行（`margin-left:auto`）
- [ ] 确认操作用 `ElMessageBox` / `el-dialog`，提示用 `toast`
- [ ] 间距/圆角走 token，不内联布局样式
- [ ] `npm run build` 通过

---

## 7. 禁止项（负面清单）

- ⛔ 不用手写 `<table>` + 百分比 colgroup 排列表。
- ⛔ 不用裸 `vw` / 裸数字做抽屉、弹窗、间距、圆角。
- ⛔ 不用浏览器原生 `confirm` / `alert`。
- ⛔ 搜索栏加「重置」按钮。
- ⛔ 不在模板用内联 `style="margin/padding/width"` 做布局（单次微调除外，要写注释）。
- ⛔ 每个页面自造一套 spacing / 圆角 / 断点。