# 插件开发路线

[English](index.md) | **简体中文**

先用[卡牌创作路线与契约（英文）](card-contract.md)选择适合的方式：已有卡牌可保存为 Legion 并打包；配置表单和被动连接可用声明式卡牌；视觉设计可用[卡包工厂](../card-factory.zh-CN.md)；工具权限、数据访问或生命周期行为再使用 Python 插件。需要自定义视图时才加入 React / TypeScript。

## 从零开始

1. [准备开发环境（英文）](setup.md)：克隆仓库，切换到文档对应的 `dev` 分支，运行 setup，使用独立的 `plugin-tutorial` 开发配置。
2. [创建第一个插件（英文）](first-plugin.md)：创建 `pyproject.toml` 和 Python 入口，声明卡片与卡包；在 Library 开包、加入牌组、放置卡片，验证配置保存。
3. [给 Agent 增加工具（英文）](agent-tools.md)：运行 Greeter 示例，了解连接如何授予对特定目标的工具权限。
4. [添加自定义界面（英文）](frontend.md)：声明前端 manifest，通过 `@oaw/plugin-api` 编辑卡片配置。
5. [测试与分发（英文）](testing.md)：验证真实入口发现、保存与重启、权限撤销和打包方式。

无需 Python 或 JavaScript 的双卡连接示例见 [Connected Notes](https://github.com/theAfish/open-agent-world/tree/dev/examples/packs/connected-notes)，对应[声明式卡牌指南（英文）](declarative-packs.md)。被动连接只保存关联，不传送数据或授予工具权限。最小 Python 示例在 [examples/plugins/hello_world](../../examples/plugins/hello_world/README.md)，不需要模型密钥。涉及 Agent 工具和生命周期的完整示例在 [Greeter](../../examples/plugins/greeter/README.md)。

## 需要更多功能时

查看 [扩展点导航（英文）](extension-points.md)，按你要实现的功能找到 API 和现有插件示例。Python 插件使用 `open_agent_world.plugin_api`，界面使用 `@oaw/plugin-api`；不要直接依赖宿主内部状态。

源码开发和发布统一使用 `oaw.pack.toml`：Python 依赖保留在原生项目文件中，前端依赖由 Pack 自己的 `package.json` 与锁文件管理。宿主适配器、独立 worker 和 Sandbox 环境分别声明，可以通过依赖组复用同一份版本约束。

前端独立构建后随 `.oawpack` 交付，安装后由宿主动态加载。开发脚本、文件安装和 Store 使用相同的依赖与卸载规则；Python wheel 是构建输入，完整发行物应为 `.oawpack`。具体命令、公共 API 和依赖回收规则见 [Pack 开发与发行指南（英文）](../pack-distribution.md)。
