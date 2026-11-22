# Controller 前端 E2E 用例

Playwright 端到端用例，覆盖 Controller 控制台外壳、TPS 三级树、用例步骤（含 SOP）、浏览器 IDE、
机器常数导入入口，以及 manual 步骤的接口契约。

- 浏览器：系统已安装的 **Microsoft Edge**（`channel: 'msedge'`），不下载 Playwright 自带浏览器。
- 执行方式：**串行**（`workers: 1`），用例共享同一个临时数据根，避免并发写同一份 TPS。
- 被测对象：`controller-web` 的构建产物 `web_dist/`，由后端挂载在 `/ui`；用例统一访问 `/ui/`。

## 1. 用例清单

| 文件 | 覆盖点 | 前置条件 | 主要断言 |
| --- | --- | --- | --- |
| `01-shell.spec.js` | 控制台外壳：左侧 5 个菜单、服务状态 | 后端 `/api/v1/health` 可用 | 5 个菜单项可见且文案正确；状态显示「已连接 · API v1」；逐个点击后 `h1` 标题与选中态同步切换 |
| `02-tps-tree.spec.js` | TPS 三级树（产品 / 子系统 / 测试台类型）、搜索过滤、新建入口 | 演示装备树 `repo/tree/tree.json` 含 `1000-A` 与 `PB-PWR-01`；种子数据含 3 份 TPS | 三级节点可见；搜索 `pb-pwr` 后计数 `1/3` 且只剩 1 条；无命中时显示「没有匹配的 TPS」；清空并展开后恢复 3 条；「＋ 新建 TPS」→ 选类型 → 创建弹层出现工序 / 解析器下拉与 BOM 设备区 |
| `03-tps-editor-steps.spec.js` | TPS 编辑器「用例步骤」：三组步骤、新增 manual、SOP 弹层 | 演示 TPS `pb-pwr-01-tps` 存在，三段各 1 条 | 前置 / 测试 / 收尾三组可见；新增条目后行数 +1；切为 manual 后出现「编辑 SOP」；弹层保存 Markdown 后弹层关闭、页面出现「SOP 已保存」、条目回填 `resource/<stepId>.md`，接口文件列表能查到该文件 |
| `04-code-ide.spec.js` | 用例代码 IDE：文件树、CodeMirror 语法高亮、保存 | 演示 TPS 含 `testcase/pb_pwr_01.py` | 文件树出现 `testcase` 目录与 `.py` 文件；点击后 `.cm-line span` 数量 > 0；追加注释后保存按钮可用，点击后出现「已保存」；接口回读内容包含新增注释 |
| `05-constants-import.spec.js` | 机器常数页两个导入入口 | 无（常数版本为空也可） | 「按子系统导入压缩包」「导入常数模型文件」可见；压缩包入口打开弹层且「开始导入」默认禁用；`.json` 入口触发原生文件选择器（不实际选文件） |
| `06-manual-step-api.spec.js` | manual 步骤接口契约（不经界面） | 预设 `PB-PWR-01`、工序 `PRC-SMT-01` | `POST /tps/scaffold` 建 TPS；追加 `type=manual + resource` 后 `PUT /tps/{id}` 成功；`GET /tps/{id}/context` 的 `steps` 含 `setup/cmd_suit/teardown` 三段，manual 条目 `case` 为空、`resource` 正确、`timeout_s` 保留 |

合计 9 个用例，正常耗时约 7 秒（不含构建）。

## 2. 启动被测服务

### 方式 A（推荐）：测试自己起停服务

不需要手工起后端。`e2e/global-setup.js` 会：

1. 在系统临时目录创建 `ate-controller-e2e-*` 数据根；
2. 把 `repo/` 下的 `tps/`、`tree/`、`processes.json`、`controller.db` 复制进去；
3. 用 `MOCK_CONTROLLER_REPO` 指向该目录，spawn `uvicorn app:app --host 127.0.0.1 --port 8071`；
4. 轮询 `/api/v1/health` 直到就绪。

`global-teardown.js` 会关闭进程并删除临时数据根。测试期间的写入（新建 TPS、SOP、代码文件）都只落在临时目录。

### 方式 B：手工起服务（临时数据根）

不想让测试管理进程时，先在 PowerShell 里准备一份临时数据根：

```powershell
$seed = Join-Path $env:TEMP "ate-ctrl-e2e"
Remove-Item -Recurse -Force $seed -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $seed | Out-Null
Copy-Item "D:\ATE\MockController\repo\tps" $seed -Recurse
Copy-Item "D:\ATE\MockController\repo\tree" $seed -Recurse
Copy-Item "D:\ATE\MockController\repo\processes.json" $seed
Copy-Item "D:\ATE\MockController\repo\controller.db" $seed

$env:MOCK_CONTROLLER_REPO = $seed
$env:MOCK_CONTROLLER_PORT = "8071"
cd "D:\ATE\MockController"
D:\ATE\MockRunner\backend\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8071
```

另开一个终端跑测试，并告诉 Playwright 用已有服务：

```powershell
cd "D:\ATE\MockController\controller-web"
$env:ATE_CTRL_URL = "http://127.0.0.1:8071"
npm run e2e
```

> 设置 `ATE_CTRL_URL` 后测试不再起停服务，也不会清理数据根；请确保它指向临时目录，而不是日常使用的 `repo/`。

## 3. 如何运行

```powershell
cd "D:\ATE\MockController\controller-web"

npm run build      # 必须：后端托管的是 web_dist，源码改动要先构建
npm run e2e        # 串行跑全部用例（headless，系统 Edge）
npm run e2e:headed # 有界面模式，便于观察
```

常用变体：

```powershell
npx playwright test e2e/02-tps-tree.spec.js          # 只跑一个 spec
npx playwright test -g "搜索框"                       # 只跑标题匹配的用例
npx playwright test --last-failed                     # 只重跑上次失败
npx playwright show-report                            # 打开 HTML 报告（失败时）
```

失败时 `test-results/` 会保留截图、trace 与 `error-context.md`；查看 trace：

```powershell
npx playwright show-trace test-results\<用例目录>\trace.zip
```

## 4. 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ATE_CTRL_URL` | 空 | 指向已有服务（如 `http://127.0.0.1:8071`）。设置后不起停后端，也不清理数据根 |
| `MOCK_CONTROLLER_REPO` | 空 | 指定后端数据根。设置后在该目录起服务，缺少种子时自动复制，跑完**不删除** |
| `MOCK_CONTROLLER_PORT` | `8071` | 后端端口；未设 `ATE_CTRL_URL` 时 `baseURL` 随之变化 |
| `MOCK_CONTROLLER_PYTHON` | `D:\ATE\MockRunner\backend\.venv\Scripts\python.exe` | 启动 uvicorn 的解释器 |

> `MOCK_CONTROLLER_REPO` 只应指向临时/一次性数据根。用例会新建 TPS、写 SOP 和代码文件；
> 指向日常使用的 `repo/` 会污染真实演示数据（默认模式不受影响，数据根是自动创建并删除的临时目录）。

## 5. 新增的 data-testid

为稳定定位补充的测试锚点（均只加属性，未改业务逻辑）：

| 文件 | testid |
| --- | --- |
| `App.vue` | `service-status`、`nav-tps` / `nav-packages` / `nav-metadata` / `nav-processes` / `nav-constants`、`page-title` |
| `views/TpsView.vue` | `tps-view`、`tps-search`、`tps-count`、`tps-tree`、`tree-expand-all`、`tree-collapse-all`、`tree-product`、`tree-subsystem`、`tree-type`、`tree-tps`、`create-tps`、`preset-picker`、`preset-picker-select`、`preset-picker-next` |
| `views/TpsEditor.vue` | `tps-editor`、`tps-save`、`tps-save-bottom`、`step-tab-setup` / `step-tab-cmd_suit` / `step-tab-teardown`、`step-add`、`step-add-empty`、`step-row`、`step-id`、`step-type`、`step-sop-edit`、`step-sop-notice` |
| `components/SopEditorDialog.vue` | `sop-dialog`、`sop-markdown`、`sop-save` |
| `components/CreateTpsDialog.vue` | `create-tps-dialog`、`create-process`、`create-parser`、`create-devices` |
| `components/CodeWorkspace.vue` | `code-file-tree`、`code-file`（含 `data-path`）、`code-dir`、`code-save`、`code-notice`、`code-editor-host` |
| `views/ConstantsView.vue` | `constants-zip-open`、`constants-json-input`、`constants-zip-dialog` |
| `components/ModalShell.vue` | `testid` prop（默认 `modal`），各弹层传入自己的值 |

## 6. 已知限制

- **只支持系统 Edge**：`channel: 'msedge'`，机器上没有 Edge 时用例无法启动；也不适用于需要 Chromium 专属能力的场景。
- **串行执行**：`workers: 1`，为共享数据根让路；不要改成并行，否则 SOP / 代码文件会互相覆盖。
- **依赖演示种子数据**：`repo/tps`、`repo/tree`、`repo/processes.json`、`repo/controller.db`。断言里写死了 `pb-pwr-01-tps`、`PB-PWR-01`、`1/3` 等；种子改动后需同步更新用例。
- **必须先构建**：`globalSetup` 会比较 `src/` 与 `web_dist/index.html` 的修改时间，源码较新时直接报错提示 `npm run build`。
- **05 不验证真实上传**：只验证入口、弹层和文件选择器；`.json` / `.zip` 的实际导入解析未覆盖。
- **03 只验证 SOP 文件落盘**：SOP 保存后的页面提示与 `resource/` 回填已覆盖，但「保存 TPS 清单」把新增 manual 条目持久化到 `tps.json` 未在 UI 层验证（接口层由 06 覆盖）。
- **06 会新建 TPS**：仅在临时数据根内；没有删除 TPS 的接口，数据随临时目录一起清理。
- **进程清理**：Windows 用 `taskkill /T /F`；若测试进程被强制中断，可能残留 8071 端口占用，重跑前先确认端口空闲。
