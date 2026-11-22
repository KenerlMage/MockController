# TPS 步骤类型编辑与 Playwright 用例说明

> 适用版本：MockController `6afeb15`、MockRunner `530ca1c`
> 实测日期：2026-11-08　环境：Windows 10 + 系统 Edge（`channel: 'msedge'`）+ Python 3.13 venv + Node 24

---

## 一、这轮改了什么

| 能力 | 位置 | 说明 |
| --- | --- | --- |
| 步骤类型可编辑 | Controller → TPS 编辑器 | 每条用例步骤可选 `auto`（自动执行）/ `manual`（人工判定），`manual` 必须挂一份 SOP |
| SOP 弹窗与判定 | Runner → 测试执行页 | 跑到 `manual` 步骤时暂停，弹窗渲染 SOP，操作员点「判定通过 / 判定失败（带备注）/ 中止测试 / 取消本轮测试」 |
| 判定结果入账 | Runner → 步骤结果 | 失败时该步记 `failed`，备注写入步骤明细，后续步骤 `skipped`，本轮任务收尾为「执行失败」 |
| 两套 Playwright 用例 | `MockController/controller-web/e2e/`、`MockRunner/frontend/e2e/` | 各自带 `README.md` 说明前置条件、断言点与运行方式 |

### manual 步骤的约束（Controller 侧强校验）

1. `type` 只允许 `auto` / `manual`，其他值直接拒绝。
2. `manual` 与 `case` 互斥：同时声明会报 `人工条目（type=manual）不能同时声明 case：manual 与 case 互斥`。
3. `manual` 必须能取到 SOP：`resource/<id>.md` 存在，或内联 `sop` 字段。只给内联 `sop` 时，保存会自动落盘成 `resource/<id>.md`，保证 Runner 侧离线也能读到同一份 SOP。
4. `manual` 与 `step=true`（单步暂停）是两件事：前者等人工判定，后者等人工放行；同一条用例不要同时用。

### 操作员决策的落点

| 决策 | 该步结果 | 后续步骤 | 本轮任务 |
| --- | --- | --- | --- |
| `pass` | `passed` | 继续 | 继续（最终看其余步骤） |
| `fail` | `failed`，明细追加 `[WARNING] 人工判定失败：<备注>` | 全部 `skipped` | `failed` / 「执行失败」 |
| `abort` | 终止 | 全部 `skipped` | `failed` |
| `cancel` | 取消本轮 | 全部 `skipped` | `cancelled` |

旧值 `next` 兼容为 `pass`，历史 TPS 不需改写。

---

## 二、Controller 用例集（9 条）

目录 `MockController/controller-web/e2e/`，配置 `playwright.config.js`，跑在由 `global-setup.js` 拉起的临时 Controller 上（临时 `MOCK_CONTROLLER_REPO`，跑完销毁，不污染 `repo/`）。

| 文件 | 用例数 | 覆盖点 |
| --- | --- | --- |
| `01-shell.spec.js` | 2 | 左侧五个菜单可见、服务状态为已连接；逐个点击切换标题与选中态 |
| `02-tps-tree.spec.js` | 3 | 装备树渲染「产品 → 子系统 → 测试台类型」三级；搜索框按名称/编号过滤并可清空恢复；新建 TPS 弹层含工序/解析器/设备下拉 |
| `03-tps-editor-steps.spec.js` | 1 | 编辑器三组步骤（前置/用例/后置）可见；新增 `manual` 条目、填 SOP、保存后出现成功提示 |
| `04-code-ide.spec.js` | 1 | 浏览器内 IDE：文件树含 `testcase`，打开 `.py` 有语法高亮，编辑后保存提示 |
| `05-constants-import.spec.js` | 1 | 常数「压缩包导入」与「单文件导入」两个入口可打开（弹层 / 文件选择器） |
| `06-manual-step-api.spec.js` | 1 | 接口契约：建 TPS → 加 `manual` 步骤 → `context.steps` 返回三段结构与 `manual` 字段 |

实测：`npx playwright test` → **9 passed (7.0s)**。

运行方式：

```powershell
cd D:\ATE\MockController\controller-web
npx playwright test            # 全量
npx playwright test e2e/03-tps-editor-steps.spec.js   # 单条
```

---

## 三、Runner 用例集（6 条）

目录 `MockRunner/frontend/e2e/`，跑在 8020 端口的 Runner 上（`frontend/dist` 由后端托管），登录态由 `auth.setup.js` 注入，单 worker 串行执行。

| 文件 | 覆盖点 |
| --- | --- |
| `01-shell.spec.js` | 左侧 6 个导航入口、跳转「测试记录」、当前登录用户显示 |
| `02-execute-page.spec.js` | 装备树、执行按钮可用、ERROR/WARNING/INFO 计数格式、测试报告区 |
| `03-tps-import.spec.js` | `input[type=file]` 同时接受 `.json`/`.zip`；上传合法 `tps.v2` 清单后提示成功、树内出现新 TPS |
| `04-manual-decision.spec.js` | 自备 TPS（1 条 `manual` + `resource/M1.md`）→ 执行 → 弹窗渲染 SOP → 判定失败 + 备注 → 步骤结果含「操作员判定失败」与备注原文 → 任务「执行失败」 |
| `05-assistant-shelf.spec.js` | 助手货架入口、来源 chip（Controller 下发/本地缓存/本地兜底）、卡片或空态、无 JS 报错 |
| `06-settings.spec.js` | 系统设置页渲染权限矩阵、无 JS 报错 |

实测：`npx playwright test` → **6 passed (32.1s)**；配套 pytest `13 passed`。

运行方式：

```powershell
# 1) 构建前端并启动被测后端（离线模式，数据隔离）
cd D:\ATE\MockRunner\frontend
npm run build

cd D:\ATE\MockRunner\backend
$env:MOCK_CONTROLLER_URL = 'http://127.0.0.1:59999'
$env:ATE_TPS_SOURCE = 'local'
$env:ATE_TPS_CACHE_DIR = Join-Path $env:TEMP 'ate-e2e-cache'
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8020

# 2) 另开窗口跑用例
cd D:\ATE\MockRunner\frontend
npx playwright test
```

---

## 四、本轮修掉的两个真实缺陷

1. **执行页显示「测试步骤（0）」、执行按钮置灰**
   根因：`/api/tps/{id}` 对来自 Controller 或缓存的清单原样返回，没有补 `steps` 派生字段，前端拿不到步骤列表。
   修复：`backend/main.py` 新增 `_normalize_for_ui()`，读取路径统一走 `tps_runtime.normalize_tps()`；本地 TPS 与远端 TPS 行为一致。

2. **manual 用例偶发超时（依赖内置 TPS 与执行顺序）**
   根因：原用例直接点内置 `spm_rh_dyn`，而装备树默认折叠、且会被前一条导入用例改写。
   修复：`04-manual-decision.spec.js` 改为在用例内用 `request` 导入自备 TPS（1 条 `manual` 步骤 + SOP），逐级展开树后选中；导入失败时给出明确跳过原因，不再空等 60 s。

---

## 五、未覆盖的范围（明确说明，不做掩盖）

- **真机链路**：所有 Runner 用例在「调试模式 + 仿真」下运行，未连接真实 SCPI/串口设备；仪器超时、重连、真实量测异常未覆盖。
- **并发与多工位**：用例串行执行，未验证多台 Runner 同时向同一 Controller 取数、同一 TPS 并发执行。
- **权限矩阵**：只验证设置页渲染，未验证不同角色（操作员/工程师/管理员）对 manual 判定按钮与编辑入口的可用性差异。
- **SOP 富文本**：SOP 按 Markdown 渲染，未覆盖图片、内嵌 HTML、超长文档（>1 MB）与外部链接点击。
- **浏览器兼容**：只在系统 Edge 上跑，Firefox/Safari 未验证；Controller 侧未做移动端视口回归。
- **超时与中断**：`manual` 的 `timeout_s` 到点行为未写用例（仅代码路径存在），断网/断电恢复未覆盖。
- **upgrade 路径**：旧 TPS（`options: next/abort/cancel`）只验证了兼容映射，未跑全量历史 TPS 回归。
- **新增 manual 条目的持久化（仅 UI 层）**：Controller 的 03 用例验证了 SOP 落盘 `resource/<stepId>.md` 与回填，但没有在界面上点「保存 TPS 清单」把新增条目写回 `tps.json`；该链路由接口层的 06 用例覆盖，UI 侧属未覆盖。

---

## 六、委派与核验（谁做了什么、谁的说法被纠正）

本轮 4 个子任务全部返回可用产出，无失败、无中止、无空回执；但自报里有两处与实际不符，均已实测纠正后才计入结论：

| 子任务 | 自报 | 实测核验 | 处理 |
| --- | --- | --- | --- |
| Controller 步骤类型 + SOP | 已完成，手动实测通过 | 复核通过：`context.steps` 三段正确、SOP 74 字节落盘可读回、manual 与 case 互斥报错准确、纯内联 sop 自动生成 `resource/M4.md` | 采纳，已提交 |
| Runner manual 判定 | 已完成，手动实测通过 | 复核通过：弹窗选项 pass/fail/abort/cancel、fail 时该步 failed 且后续 skipped、任务 failed | 采纳，已提交 |
| Controller Playwright 用例 | 「6 条用例」 | 实际 **9 条**（6 个文件），我实跑 9 passed / 7.0s | 条数说法偏少，按实际 9 条记录，已提交 |
| Runner Playwright 用例 | 「6 条全绿」 | 实际首次 **5 passed / 1 failed**（`04-manual-decision` 超时）；修复后 6 passed / 32.1s | 自报不成立，已修复并重跑；修复过程中发现并修掉真实缺陷「`/api/tps/{id}` 未归一化 steps」 |

未覆盖范围见第五节，未由任何子任务认领的部分（真机链路、并发、角色权限、`timeout_s` 到点、历史 TPS 全量回归）在本轮均未实现，不作为已完成交付。
