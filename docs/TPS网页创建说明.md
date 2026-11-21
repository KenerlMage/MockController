# TPS 网页创建与在线编辑（功能说明与已知限制）

日期：2026-11-21　工程：MockController　涉及：`processes.py`、`tps_scaffold.py`、`api_v1.py`、`tps_store.py`、`controller-web/`

## 一、功能范围

### 入口

1. **测试台类型右键创建**：控制台 →「元数据」→ 三级装备树 → 在测试台类型节点上右键 →「创建 TPS」；
2. **TPS 管理页**：已创建 TPS 的列表、选中后进入编辑器；
3. **工序页**：侧栏「工序」（与元数据同级），维护工序清单。

### 创建时用选择代替手填

| 字段 | 来源 | 是否必填 |
| --- | --- | --- |
| 产品 / 子系统 / 测试台类型 | 右键节点带入（只读） | 自动 |
| 工序 | `/api/v1/processes`（8 条 mock 数据） | 可空 |
| 解析器版本 | `/api/v1/tps/parsers`（tps.v2 默认 / tps.v1） | 默认 tps.v2 |
| 机器常数版本 / PN / 模型 | `/api/constants/*`，模型多选 | 可空 |
| 使用设备 | 该测试台类型 BOM 的 `alias`，默认全选 | 可空 |
| TPS 编号 / 名称 | 留空由后端按类型 + 工序生成 | 可空 |

### 后端接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/processes` | 工序清单 |
| GET | `/api/v1/processes/{id}` | 工序详情 |
| POST | `/api/v1/processes` | 新增/更新工序 |
| DELETE | `/api/v1/processes/{id}` | 删除（被 TPS 引用返回 409） |
| GET | `/api/v1/tps/parsers` | 解析器清单 |
| POST | `/api/v1/tps/scaffold` | 脚手架：从测试台类型 + BOM 生成合法清单与起始用例文件 |
| GET | `/api/v1/tps/{id}/context` | 编辑器上下文（tps / preset / bom / processes / parsers / constants） |
| GET | `/api/v1/tps/{id}/files` | 文件清单 |
| GET | `/api/v1/tps/{id}/files/{path}` | 读文件（二进制安全） |
| PUT | `/api/v1/tps/{id}/files/{path}` | 写文本文件（后缀白名单、防穿越、2 MB 上限） |
| DELETE | `/api/v1/tps/{id}/files/{path}` | 删除文件 |

### 保存校验

- `parser.name` 必须在解析器清单内；
- `process.id` 必须存在于工序清单；
- `preset_id` 必须存在于装备树；
- `device_config` 的键若不是该类型 BOM 的 alias，只返回 warning，不阻断保存。

## 二、实测记录（2026-11-21）

| 场景 | 结果 |
| --- | --- |
| 工序接口 | 8 条 |
| 解析器接口 | tps.v2 / tps.v1，v2 为默认 |
| 脚手架（SPM-RH-DYN-01 + PRC-FCT-04 + 3 设备 + V-CTL-1） | 清单合法；preset/product/subsystem/dut/parser/process/device_config(3)/constant_model 均带入；生成 `testcase/spm_rh_dyn_01.py` |
| 写代码文件 | PUT 64 字节，GET 回读一致 |
| 路径穿越 `../evil.py` | 404 |
| 非法后缀 `.exe` | 400 |
| 非法解析器保存 | 400，提示可选 tps.v2 / tps.v1 |
| context 聚合 | preset / bom=7 / processes=8 / parsers=2 / constants.active=V-CTL-1 |
| 浏览器右键菜单 | 出现「创建 TPS」「查看 BOM」 |
| 创建弹层 | 工序 8 项、解析器 2 项、常数版本 2 项、设备 7/7 全选 |
| 弹层提交 | 生成 `spm-rh-dyn-01-tps-2`，自动跳转编辑器 |
| 网页改代码并保存 | 提示「已保存（1211 字节）」，接口回读含修改标记 |
| Runner 读取 | `/api/tps` 与详情均可见，`parser=tps.v2`、`process=FCT-04`、`devices=7` |

## 三、已知限制（未覆盖范围）

以下为**明确未实现或未验证**的部分，不要当作已完成：

1. **代码编辑器无语法高亮与补全**：纯 `<textarea>` + 行号列 + Tab 缩进，未引入 CodeMirror/ACE；保存前不做 Python 语法检查，语法错误要等 Runner 执行时才暴露。
2. **文件删除无界面入口**：`DELETE /files/{path}` 已实现，前端只做了保存与新建文件；删除需调用接口。
3. **设备与 BOM 的一致性只在保存时告警**：BOM 中删除某设备后，已有 TPS 的 `device_config` 不会自动清理，也不会在编辑器中标红。
4. **工序为 mock 数据**：`repo/processes.json`，未对接 MES/工艺系统；`product_scope` 字段只存储，创建弹层未按产品过滤工序。
5. **Runner 未实际执行新 TPS**：只验证 Runner 能读到并展示该 TPS；未跑一次完整用例（`testcase/` 靠 Runner 首次读取时物化）。
6. **无并发压测**：`processes.json` 与 `tps.json` 仅进程内锁 + 原子替换写，多进程/高并发未验证。
7. **tps.v1 清单不受解析器清单约束**：校验只在清单含 `parser` 字段时生效，老 v1 清单通常没有该字段。
8. **编辑器不支持二进制文件预览**：`GET /files/{path}` 可读任意文件，但前端只对 `.py/.md` 做文本编辑。

## 四、运行前置

1. 工序数据首次访问时自动落种到 `repo/processes.json`（`repo/` 已被 git 忽略，换机器需重新落种）；
2. 前端构建产物在 `web_dist/`，由 Controller 在 `/ui` 托管；改前端后需 `npm run build`；
3. 创建 TPS 前该测试台类型应已有 BOM，否则设备区为空（界面会提示先补 BOM）。
