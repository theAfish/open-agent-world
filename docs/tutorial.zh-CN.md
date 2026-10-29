# 维护交互式教程

欢迎页提供 **Start Tutorial（开始教程）**、**Choose a workspace（选择工作空间）**、**Start blank（从空白开始）**。用户操作说明见[第一个任务](user-guide/first-team.md)。画布教程还覆盖 Sandbox、粘合、Minister、Legion 和工作区布局；模型步骤可稍后完成，教程不会自动启动 Sandbox。

## 修改现有教程

- [steps.ts](../frontend/src/onboarding/steps.ts) 定义稳定步骤 ID、文案、目标和完成条件。
- [controller.ts](../frontend/src/onboarding/controller.ts) 管理持久化进度、演示、恢复和临时道具。
- [Onboarding.tsx](../frontend/src/onboarding/Onboarding.tsx) 提供欢迎选项、向导位置和操作按钮。
- [interactionGuard.ts](../frontend/src/onboarding/interactionGuard.ts) 限定当前步骤可用的操作；修改步骤时一起检查。
- [messages.json](../frontend/src/i18n/messages.json) 保存中英文界面文案。

复用普通卡片操作和权限选择。不要为教程实现另一套业务行为。改文案时保持步骤 ID，避免重置进度。演示必须可取消，临时道具必须登记；清理保留用户编辑及关联的用户对象。刷新后暂停操作，恢复根据权威状态判断，不能把视口中看不到卡片当作已经删除。

根据改动运行相关 onboarding Vitest 与现有 `onboarding.spec.ts`、`onboarding-legion.spec.ts` 浏览器场景，覆盖完整手势、暂停恢复和减少动态效果。浏览器通过不代表真实模型或原生隔离运行时已经验证。

## 卡片自带教程

插件可定义[渐进式教程](developers/progressive-tutorials.md)，通过 **帮助 → 卡片教程与文档** 访问。文案应使用卡片实际 UI 名称。手动“继续”不表示宿主已观测或代为执行操作。
