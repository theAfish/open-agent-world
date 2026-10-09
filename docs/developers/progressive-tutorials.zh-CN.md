# 渐进式卡片教程

[English](progressive-tutorials.md)

Plugin API **1.26** 为卡片与卡包增加可选的 `tutorials` 声明。宿主统一提供提示、阅读进度、关闭记录和离线文档，不需要为每个卡包开发教程界面。旧插件继续兼容。

## 用户如何使用

首次放置、选择、展开或查看卡片时，会提示相关教程；打开卡包也能触发卡包教程。后台加载画布和已有收藏不会触发。每次只显示一个提示，用户可以选择**查看教程**，也可以直接关闭。关闭会记住当前步骤，并阻止该教程以后自动弹出。

通过**帮助 → 卡片教程与文档**可以搜索、继续阅读、重看已完成的教程，或阅读文档。这里也能关闭全部自动提示。已停用或卸载的插件不会继续展示内容，但阅读记录会保留。

首次使用主线教程期间忽略新的触发；拖动、连线或其他弹窗打开时暂时隐藏提示。阅读不会自动放置卡片、授予权限、运行工具或发送消息。

## 为自己的卡片定义教程

```python
from open_agent_world.plugin_api import TutorialDefinition, TutorialStep

INTRO = TutorialDefinition(
    id="first-use",
    revision=1,
    title={"en": "First use", "zh-CN": "首次使用"},
    summary="用两个步骤了解这张卡片。",
    steps=(
        TutorialStep(id="input", title="填写输入", body="打开卡片并填写名称。"),
        TutorialStep(id="run", title="查看结果", body="点击 **Run** 查看输出。"),
    ),
    document="## 使用说明\n\n在这里描述输入、输出和使用示例。",
)
# NodeTypeDefinition(..., tutorials=(INTRO,))
# PackDefinition(..., tutorials=(INTRO,))
```

卡片与卡包使用同一个声明。教程 ID 在所属卡片类型或卡包内唯一，不会因为放置多张同类型卡片而重复弹出。插件描述符与 `.oawpack` 的 `compatibility.plugin_api` 应声明为 `1.26`。完整示例见仓库中的 `examples/packs/greeter`。

- `steps` 中每一步包含稳定的 `id`、标题和 Markdown 正文，用户点击继续后前进。
- `document` 提供完整文档；仅定义文档、不定义步骤时，内容只出现在帮助目录中。
- `trigger="manual"` 关闭该教程的自动提示，但保留手动入口。
- `after=("first-use",)` 使自动提示等待同一卡片类型／卡包下的前置教程完成。关闭前置教程不会将其标为完成；手动阅读始终可用。
- `revision` 默认 1；大幅更新内容时递增。旧版本已完成的教程可以再次提示，用户明确关闭的教程仍不打扰。
- 文本可以直接用字符串，也可以用语言映射；语言映射必须提供 `en` 作为回退。每个所属对象最多 50 个教程，每个教程最多 50 步。

ID 重复、前置关系缺失或成环、空内容与无效语言映射会在插件安装时被拒绝。

## 无代码卡包与架构

Schema 2 内容卡包支持在 `manifest.json` 的 `creator.tutorials` 中填写同结构的 JSON 数组；单卡 recipe 则在顶层 `tutorials` 中填写，与 `id`、`design` 并列。导出请求也支持 `creator.tutorials`。声明采用纯数据，不运行 Python 或 JavaScript。

当前通过 Python／JSON 编写教程，卡牌工厂尚未加入可视化教程编辑器。当前步骤采用手动继续；不包含自定义执行条件、自动操作、任意 DOM 定位或工具调用。

Markdown 支持文本、列表、代码和表格。HTTP(S) 链接由用户点击后在新标签页打开；不渲染原始 HTML，不加载图片，图片显示替代文字。内嵌文档可离线阅读。

代码分为数据与校验（`backend/plugins/tutorials.py`）、目录适配、独立进度引擎（`frontend/src/tutorials/store.ts`）、交互观察器和展示组件。独立进度引擎可注入存储，不依赖画布或首次使用教程。进度通过现有应用配置存储按 profile 保存，仅包含教程 ID、修订版本、步骤与状态，不保存卡片内容或凭据。开发面板的教程重置会一起清除渐进式教程记录。
