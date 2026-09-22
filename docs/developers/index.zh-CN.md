# 插件开发路线

插件是向 OAW 注册卡片、关系、工具或界面的 Python 包。先用 Python 做一张带配置字段的卡片；需要自定义界面时再加入 React / TypeScript。

## 从零开始

1. [准备开发环境（英文）](setup.md)：克隆仓库，切换到文档对应的 `dev` 分支，运行 setup，使用独立的 `plugin-tutorial` 开发配置。
2. [创建第一个插件（英文）](first-plugin.md)：创建 `pyproject.toml` 和 Python 入口，声明卡片与卡包；在 Library 开包、加入牌组、放置卡片，验证配置保存。
3. [给 Agent 增加工具（英文）](agent-tools.md)：运行 Greeter 示例，了解连接如何授予对特定目标的工具权限。
4. [添加自定义界面（英文）](frontend.md)：声明前端 manifest，通过 `@oaw/plugin-api` 编辑卡片配置。
5. [测试与分发（英文）](testing.md)：验证真实入口发现、保存与重启、权限撤销和打包方式。

最小示例在 [examples/plugins/hello_world](../../examples/plugins/hello_world/README.md)，不需要模型密钥。涉及 Agent 工具和生命周期的完整示例在 [Greeter](../../examples/plugins/greeter/README.md)。

## 需要更多功能时

查看 [扩展点导航（英文）](extension-points.md)，按你要实现的功能找到 API 和现有插件示例。Python 插件使用 `open_agent_world.plugin_api`，界面使用 `@oaw/plugin-api`；不要直接依赖宿主内部状态。

前端插件在 OAW 构建时发现。只安装 Python wheel 不会把新界面加入现有桌面安装包，分发时应明确兼容版本与构建步骤。
