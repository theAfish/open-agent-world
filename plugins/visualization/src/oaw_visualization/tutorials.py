"""Offline tutorials and reference documents belong to the visualization Pack."""
from importlib.resources import files

from open_agent_world.plugin_api import TutorialDefinition, TutorialStep


def localized(en, zh):
    return {"en": en, "zh-CN": zh}


REFERENCE = {
    locale: files(__package__).joinpath(f"docs/guide.{locale}.md").read_text(encoding="utf-8")
    for locale in ("en", "zh-CN")
}

PACK_TUTORIALS = (TutorialDefinition(
    id="first-chart", title=localized("Visualize a connected dataset", "将数据库画成图"),
    summary=localized("Connect a source, choose its schema, then select the fields to plot.",
                      "连接数据源时选择 schema，再在卡片内选择要绘制的字段。"),
    steps=(
        TutorialStep(id="choose-card", title=localized("Choose a chart", "选择图表卡片"),
            body=localized("Place a **Line chart**, **Bar chart**, **Scatter plot**, **Distribution**, or **Graph** card. It opens in a large details view. Place a SQL database or MKB card with data beside it.",
                           "放置**折线图、柱状图、散点图、分布统计图或关系图**卡片，它会直接展开详情。在旁边放置已有数据的 SQL 数据库或 MKB 卡片。")),
        TutorialStep(id="connect-schema", title=localized("Select the schema while connecting", "连线时选择 schema"),
            body=localized("Drag between the chart and database, or choose the database in **Source**. In **Connect data source**, choose **Schema**, then **Connect**. Cancelling leaves no connection. SQL offers tables and views; MKB offers structured records and its published graph where compatible.",
                           "在图表与数据库之间拖线，或在**数据源**中选择数据库。在**连接数据源**弹窗中选好 **Schema**，再点击**连接**；取消不会留下连线。SQL 可选表和视图，MKB 可选结构化记录及适合关系图的已发布知识图谱。")),
        TutorialStep(id="choose-fields", title=localized("Choose fields and explore", "选择字段并查看图表"),
            body=localized("Select **X/Y** and optional **Series** inside the card. For graph tables, select **From/To**; for distributions, select **Value**. Scroll to zoom, drag to pan, and double-click to reset. Click **Refresh data** after source data changes. **Partial data** means the chart uses a bounded result.",
                           "在卡片内选择 **X/Y** 和可选的**分组**；表格关系图选择**起点/终点**，分布统计图选择**数值**。滚轮缩放、拖动平移、双击复位。源数据更新后点击**刷新数据**；出现**部分数据**时，图表只覆盖当前返回的数据。")),
    ), document=REFERENCE,
),)

CARD_TUTORIALS = (TutorialDefinition(
    id="chart-reference", trigger="manual", title=localized("Data visualization guide", "数据可视化使用指南"),
    summary=localized("Chart fields, schema selection, limits and reconnecting.", "图表字段、schema 选择、数据上限与重新连接。"),
    document=REFERENCE,
),)
