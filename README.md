# MockController

装备软件的归档与下载服务，与 MockRunner 同级（同在 `D:\ATE` 下）、独立部署、独立发版。MockRunner 通过 HTTP 拉取指定 `object@version` 的软件包，校验 `sha256` 后解压执行包内脚本，并按包内 `probe` 验收。

## 工程位置

```
D:\ATE\
├── MockController\   ← 本工程（配置与归档中心）
└── MockRunner\       ← 执行端
```

## 与其他工程的关系

| 工程 | 职责 | 依赖方向 |
| --- | --- | --- |
| MockController（本工程） | 包归档、版本管理、manifest 生成与下载 | 无 |
| MockRunner | 任务执行、按 TPS 声明部署、状态回传 | 调用 Controller 的 HTTP 接口 |

两边只通过 HTTP 契约耦合：Controller 不知道谁在下载，Runner 不访问 Controller 的存储目录。

## 启动

```bat
cd D:\ATE\MockController
start_controller.bat
```

默认监听 `0.0.0.0:8070`，仓库根 `repo/`（随程序目录推导）。环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MOCK_CONTROLLER_REPO` | `<程序目录>\repo` | 数据根（包 / TPS / 树 / 库），旧名 `ATE_CONTROLLER_REPO` 仍兼容 |
| `MOCK_CONTROLLER_HOST` | `0.0.0.0` | 监听地址（旧名兼容） |
| `MOCK_CONTROLLER_PORT` | `8070` | 监听端口（旧名兼容） |

## 目录结构

```
D:\ATE\MockController\
├── app.py                  # FastAPI 服务（包归档 / 静态前端）
├── requirements.txt
├── start_controller.bat
├── .gitignore
└── repo\                   # 运行时数据，不入库
    └── <object>\
        └── <version>\
            ├── package.zip
            └── manifest.json
```

## 接口

### 包（`/api`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/packages` | 包清单 |
| GET | `/api/packages/{object}` | 单包详情（各版本） |
| GET | `/api/packages/{object}/{version}/manifest` | manifest |
| GET | `/api/packages/{object}/{version}/download` | 下载 zip（响应头带 `X-Package-Sha256`） |
| POST | `/api/packages` | 归档上传 |

### TPS 与常数（`/api/v1`、`/api/constants`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康 + TPS 统计 |
| GET | `/api/v1/capabilities` | 能力协商 |
| GET | `/api/v1/tps` | TPS 列表（摘要） |
| GET | `/api/v1/tps/{id}` | TPS 全文 |
| PUT | `/api/v1/tps/{id}` | 保存 |
| POST | `/api/v1/tps/import` | JSON 导入 |
| POST | `/api/v1/tps/import/zip` | zip 导入（含 testcase / resource） |
| GET | `/api/v1/tps/{id}/files` | 附带文件清单 |
| GET | `/api/v1/tps/{id}/files/{path}` | 附带文件内容 |
| GET | `/api/constants/versions` | 常数版本列表 |
| POST | `/api/constants/import` | 常数包导入 |

### TPS 与常数（`/api/v1`、`/api/constants`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康 + TPS 统计 |
| GET | `/api/v1/capabilities` | 能力协商 |
| GET | `/api/v1/tps` | TPS 列表（摘要） |
| GET | `/api/v1/tps/{id}` | TPS 全文 |
| PUT | `/api/v1/tps/{id}` | 保存 |
| POST | `/api/v1/tps/import` | JSON 导入 |
| POST | `/api/v1/tps/import/zip` | zip 导入（含 testcase/resource） |
| GET | `/api/v1/tps/{id}/files` | 附带文件清单 |
| GET | `/api/v1/tps/{id}/files/{path}` | 附带文件内容 |
| GET | `/api/constants/versions` | 常数版本列表 |
| POST | `/api/constants/import` | 常数包导入 |

## 归档规则

`POST /api/packages` 表单字段：`object`、`name`、`version`、`entry`、`file`，可选 `platform`、`probe_script`、`probe_timeout_s`。

硬校验（任一不通过返回 400）：

1. `object` / `version` 只允许字母、数字、下划线、点、短横线；
2. `entry` 必须是包内相对路径，不含盘符与 `..`；
3. `entry` 必须真实存在于 zip 中（只读 zip 中央目录，不解压）；
4. 脚本后缀与 `platform` 匹配：`.ps1/.bat/.cmd/.exe` → windows，`.sh` → linux，`.py` 需显式指定 `platform`；
5. 包内根目录不允许出现 `manifest.json`（manifest 由本服务生成，避免两处版本号并存）；
6. 同一 `object@version` 已存在返回 409，修 bug 必须发新版本。

## manifest

由本服务在归档时生成，与 `package.zip` 并列存放，是部署决策的唯一事实源：

```json
{
  "object": "probe_ok", "name": "探测演示包", "version": "1.0.0",
  "entry": "deploy/install.bat", "platform": "windows",
  "sha256": "80fe00…", "size": 440, "uploaded_at": "2026-11-20T14:14:13+08:00",
  "deploy": { "probe": { "script": "deploy/probe.bat", "timeout_s": 10, "expect_exit_code": 0 } }
}
```

`probe` 由包上传时给出，Runner 用它在部署前判断是否已安装、部署后验收。探测脚本输出单行 JSON：

```json
{"installed": true, "version": "1.0.0"}
{"installed": false, "detail": "state file missing"}
```

## 归档示例

```powershell
curl.exe -X POST http://127.0.0.1:8070/api/packages `
  -F "object=spm_rh_dyn" -F "name=SPM读头动态测试软件" -F "version=1.2.0" `
  -F "entry=deploy/install.ps1" -F "probe_script=deploy/probe.ps1" `
  -F "file=@D:\build\spm_rh_dyn-1.2.0.zip"
```

包内建议结构：

```
package.zip
├── deploy/install.ps1     # entry 指向
├── deploy/probe.ps1       # probe 指向
└── bin/...
```

## TPS 模块

TPS 的唯一事实源在本服务，Runner 通过 `/api/v1/tps*` 只读拉取，本地仅保留运行副本与缓存。

存储：`repo/tps/<tps_id>/tps.json`（清单）+ `testcase/`、`resource/`（附带文件）。

校验：`tps_schema.py` 与 Runner 现有校验同源（`tps.v1` / `tps.v2`），不合格直接 400，列全部错误。

摘要字段：`has_constants`、`constant_model_count`、`deploy_count`，供列表页与执行页直接使用。

## 常数库

机器常数模型库（`constant_version` / `constant_model` / `constant_value`）已迁入本服务，路由仍为 `/api/constants/*`，数据落 `repo/controller.db`。

## TPS 模块

TPS 的唯一事实源在本服务；Runner 通过 `/api/v1/tps*` 只读拉取，本地只保留运行副本与缓存。

存储：`repo/tps/<tps_id>/tps.json`（清单）+ `testcase/`、`resource/`（附带文件）。

校验：`tps_schema.py` 与 Runner 现有校验同源（`tps.v1` / `tps.v2`），不合格直接 400 并列出全部错误。

摘要字段：`has_constants`、`constant_model_count`、`deploy_count`，列表页与执行页可直接使用。

## 常数库

机器常数模型库（`constant_version` / `constant_model` / `constant_value`）已迁入本服务，路由仍为 `/api/constants/*`，数据落 `repo/controller.db`。

## 版本策略

- 已发布版本不可覆盖，回滚通过安装旧版本实现；
- 删除版本需人工操作仓库目录，并同步清理调用方记录；
- 包清单每次由目录扫描重建，不存在独立的索引文件。
