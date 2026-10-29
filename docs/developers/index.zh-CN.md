# 开发 Card App

Card App 为卡片提供行为和可选的自定义界面；Pack 是分发这些贡献的安装包。当前完整开发路线是**外部 IDE + 公开 Python / React SDK + Coding Agent**。

## 完成一个实际项目

从[Greeter Card App 完整教程](card-app.md)开始，在同一个项目里依次完成：

1. 最小可运行 Python 卡片与独立开发档案。
2. 配置验证和生命周期数据。
3. Agent 连接、关系、工具授权及撤销测试。
4. 使用宿主 SDK 的 React/TypeScript 界面。
5. 构建 wheel 与前端，再打包 Schema 3 `.oawpack`。
6. 在干净宿主中安装、重启并检查运行结果。

这里对应 **dev**。按目标宿主版本开发；最低 Plugin API 要求不代表所有已发行安装包都包含该功能。

## 根据任务选择路径

| 需求 | 路径 |
| --- | --- |
| 只有可保存的表单字段，没有 Python 行为 | [声明式卡包快速入门](quickstart-pack.md)（英文） |
| 不写代码设计卡面与基本表单运算 | [卡片工厂](../card-factory.md) |
| 分享配置好的团队 | [内容卡包导出](../creator-packs.md)（英文） |
| 在 OAW 内编辑源码项目 | [Pack IDE（实验性）](pack-ide.md)（英文） |

传统 Factory 制作工具继续提供用户操作流程。Pack IDE 位于 **Developer tools**，尚在开发；完整 Python/React 项目以外部 SDK 路线为主。

## 给 Coding Agent 的入口

先给它本页、[完整教程](card-app.md)、宿主 revision、预期行为和安装目标。要求它使用 `open_agent_world.plugin_api` 与 `@oaw/plugin-api`，明确分发 ID、运行时 owner、Pack ID 和卡片类型 ID，复用宿主配置、文档、状态及实时图授权。

需要精确字段时查[API 目录](../plugins.md)和[Pack 契约](../pack-distribution.md)，不要猜测私有端点或导入宿主内部实现。交付时实际构建并在独立数据目录安装，注明测试层次和未验证项目。

进一步开发可查询[文档与动作](../reference/plugin-documents.md)、[工作区界面](workspace-design.md)、[数据源](data-sources.md)、[执行来源](execution-graph.md)和[卡片教程](progressive-tutorials.md)。这些参考部分为英文。
