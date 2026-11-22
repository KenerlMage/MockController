# Controller TPS 管理与常数导入（本轮交付说明）

日期：2026-11-22　工程：MockController / MockRunner

## 一、TPS 三级管理

控制台「TPS 管理」改为四级树：**产品 → 子系统 → 测试台类型 → TPS**。

| 能力 | 说明 |
| --- | --- |
| 归组 | TPS 按 `product` / `subsystem` / `preset_id` 归入装备树节点；无法归组的进入「未归类」 |
| 新建入口 | 子系统节点与测试台类型节点都提供「＋ 新建 TPS」；子系统入口打开弹层时先选该子系统下的类型 |
| 标记 | TPS 节点显示 `C`（有常数绑定，数量）与 `📦`（有部署条目） |
| 检索 | 顶部搜索按编号/名称过滤，命中路径自动展开 |
| 列表字段 | `GET /api/v1/tps` 每条带 `preset_id / product / subsystem / parser / process_id / device_count / constant_binding_count` |

## 二、用例代码：浏览器 IDE

新增 `controller-web/src/components/CodeWorkspace.vue`：

| 区域 | 能力 |
| --- | --- |
| 左侧文件树 | 从 `GET /files` 的扁平路径构建目录树；`＋ 文件`、`＋ 文件夹`、重命名、删除；`.py` 归「源码」，`.md/.json/.yaml` 归「资源」；可切换显示隐藏项 |
| 右侧编辑器 | CodeMirror 6 + `@codemirror/lang-python`：Python 语法高亮、行号、括号匹配、代码折叠、Tab 缩进 4 空格、`Ctrl+S` 保存 |
| 保存 | `PUT /api/v1/tps/{id}/files/{path}`，保存后提示「已保存 路径（N 字节）」，未保存显示 `● 未保存` |
| 切换保护 | 有未保存修改时切换文件会提示「保存后切换 / 仍然切换 / 取消」 |

构建产物：`index-P7X4Jk8M.js` 约 545 KB（CodeMirror 占主要部分），构建正常。

## 三、机器常数多行绑定

- TPS 清单新增 `constant_bindings: [{version, pn, models:[...]}, ...]`，可任意多行；
- 兼容旧 `constant_model`：读到旧字段自动派生为一行；保存时 `constant_model` 同步为第一行，Runner 旧读法不受影响；
- `GET /api/v1/tps/{id}/context` 的 `constants` 提供 `versions / catalog / bindings`，编辑器下拉直接用 `catalog`（按版本 + PN 分组）；
- 列表摘要新增 `constant_binding_count`。

## 四、常数按子系统导入

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/constants/subsystems` | 列出装备树里的产品/子系统及其已有 PN，供界面下拉 |
| `POST /api/v1/constants/import-bundle` | 多文件 JSON 合并导入（同一版本、同 PN 同 model_key 视为冲突） |
| `POST /api/v1/constants/import-zip` | 压缩包内多个 `.json`（含一层子目录）批量导入，返回逐文件结果 |
| `POST /api/v1/constants/import-file` | 直接导入单个常数模型文件（全量包或单模型） |

规则：所有文件先整体解析校验，**任一失败则全部不落库**并返回 400 指明文件；调用方给出 product/subsystem 时自动为每个 group 补全归属。界面在「机器常数」页提供「按子系统导入压缩包」与「导入常数模型文件」两个入口。

## 五、修复的数据根缺陷

- `metadata.py` 与 `app.py` 的数据根原先依赖进程工作目录，换目录启动会读到空树；
- 现统一按工程目录解析（相对路径一律相对 `MockController/`），并支持 `--repo` 风格的环境变量覆盖；
- 新增 `testresource/testbench_presets.json` 种子（2 产品 / 3 子系统 / 7 测试台类型 / 49 BOM 器件），新环境首次启动自动建树。

## 六、测试用例

| 工程 | 用例 | 结果 |
| --- | --- | --- |
| MockController | `tests/test_configurator.py`（8 项：装备树三级、TPS 分组字段、脚手架新建、常数多行、zip 导入、单文件导入、坏文件拒绝、子系统列表） | **8 passed** |
| MockRunner | `backend/tests/test_runner_smoke.py`（7 项：健康、前端托管、助手清单、TPS 来自 Controller、元数据转发、写操作拒绝、停机后缓存兜底） | **7 passed** |

运行方式：

```bat
cd D:\ATE\MockController && ..\MockRunner\backend\.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp
cd D:\ATE\MockRunner\backend && .venv\Scripts\python.exe -m pytest tests\test_runner_smoke.py -q --basetemp=tests\.pytest_tmp
```

Runner 用例会真实拉起一个 Controller（独立临时数据目录）再拉起 Runner，验证两系统联动；离线兜底用例排在最后，会停掉 Controller。

## 七、已知限制

1. 编辑器无自动补全与跳转定义，仅语法高亮与基础编辑；
2. 保存前不做 Python 语法检查，语法错误要等 Runner 执行才暴露；
3. `context` 的 `catalog` 每次遍历所有版本，版本/PN 很多时打开编辑器会变慢，未做缓存；
4. 前端主包体积偏大（CodeMirror 未做代码分割），构建有 500 KB 警告；
5. Runner 用例依赖 `D:\ATE\MockController` 存在，缺该工程时会 skip 联动部分。
