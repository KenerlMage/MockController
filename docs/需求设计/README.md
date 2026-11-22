# 需求设计（Controller 备份）

本目录是 Controller 需求设计的**规范备份**；统一查阅入口在 MockRunner 仓库：

```
D:\ATE\MockRunner\docs\需求设计\
├── controller/   # 本目录同名文件的规范原件
└── runner/       # Runner 侧 SR 抽取
```

## 约定

- 修改顺序：Runner `docs/需求设计/controller/` 原件 → 覆盖到本目录 → 若有 SR/AR 增删，同步重新生成 Runner 侧抽取。
- 本目录文件名与 Runner 侧 `controller/` 完全一致，便于对比与覆盖。
- 格式按《需求设计》skill：SR/AR 两级，三字段（需求价值 ≤50 字、设计目标 ≤100 字、需求描述 ≤200 字）。

## 文件

| 文件 | 内容 |
| --- | --- |
| [机器常数功能模块.md](机器常数功能模块.md) | SR01 Controller 归档/审批/目录 + SR02 Runner 标定执行，共 19 个 AR |
| [机器常数实现契约.md](机器常数实现契约.md) | 并行实现的接口、表结构、验收口径 |
| [机器常数实现说明.md](机器常数实现说明.md) | 落地状态、实测数据、未覆盖范围、委派核验 |
| [软件部署功能.md](软件部署功能.md) | SR01 Controller 归档与部署编排、SR02 Runner 三类部署、SR03 Controller Python 依赖供给、SR04 Runner 依赖环境执行，共 27 个 AR |
