"""Bundled authoring tools; exported recipes contain data only."""
from dataclasses import replace

from backend.packs.factory_cards import recipe_definition
from backend.packs.factory_models import CardRecipe, EmptyConfig, FaceDesign, FunctionDesign, PackDesign, PackerConfig, PrintedCard
from open_agent_world.plugin_api import NodeTypeDefinition, NodePresentation, PackDefinition, PluginDefinition, PluginDescriptor, RelationshipDefinition
from open_agent_world.plugin_api import LegionPresetDefinition, PresetNode, PresetEdge, NodeTemplateHandler

OWNER = "oaw.factory"


class PackerTemplate(NodeTemplateHandler):
    def capture_config(self, node):
        return {**node.config, "items": []}


def register(registration):
    tools = [
        ("pack", "卡包设计器", "设置包装、名称与发布信息", "package", PackDesign),
        ("face", "卡面设计器", "设计卡面、材质与功能区域布局", "palette", FaceDesign),
        ("function", "功能设计器", "表单字段、文本模板与简单运算", "settings", FunctionDesign),
        ("printer", "卡牌印刷器", "连接卡面与功能，印刷一张独立预设卡牌", "printer", EmptyConfig),
        ("packer", "打包器", "拖入卡牌与 Legion，设置通用参数并导出分享", "package", PackerConfig),
    ]
    for key, name, description, icon, config in tools:
        registration.register_node_type(NodeTypeDefinition(id=f"{OWNER}.{key}", label=name, description=description,
            icon=icon, color="#aa7144", deck_id="factory", deck_label="卡包工厂", deck_icon="package", default_name=name,
            default_size=(360, 310), default_status="idle", statuses=frozenset({"idle"}), config_model=config,
            frontend={slot: key for slot in ("preview", "body", "settings", "workspace")},
            presentation=NodePresentation(states=("node", "preview", "inspector", "workspace"), initial="preview", open="workspace"),
            card_face={"variant": "compact", "tone": "sand"}, templateable=True,
            template_handler=PackerTemplate() if key == "packer" else None))
    registration.register_node_type(replace(recipe_definition(CardRecipe(id=f"{OWNER}.card", design=PrintedCard())), user_creatable=False))
    for key, source, target in [("face", "face", "printer"), ("function", "function", "printer"), ("pack", "pack", "packer")]:
        registration.register_relationship(RelationshipDefinition(id=f"{OWNER}.{key}-input", label="设计输入", short_label="设计",
            description="将已保存的设计提供给工厂设备", source_types=frozenset({f"{OWNER}.{source}"}),
            target_types=frozenset({f"{OWNER}.{target}"}), templateable=True))
    registration.register_pack(PackDefinition(id=OWNER, name="卡包工厂", description="设计 → 印刷 → 组合打包 → 分享",
        cards=tuple(f"{OWNER}.{key}" for key, *_ in tools) + (f"{OWNER}.card",), packaging="collector", accent_color="#aa7144"))
    registration.register_legion_preset(LegionPresetDefinition(id=f"{OWNER}.workshop", name="卡包工厂工作台",
        description="已连接好的五台设备：设计、印刷、打包和分享。双击设备打开编辑器。",
        nodes=tuple(PresetNode(key=key, type=f"{OWNER}.{key}", name=name, parent_key=None,
            x=x, y=y) for key, name, x, y in [
                ("face", "卡面设计器", 0, 0), ("function", "功能设计器", 0, 650),
                ("printer", "卡牌印刷器", 700, 300), ("pack", "卡包设计器", 1400, 0), ("packer", "打包器", 1400, 650)]),
        edges=tuple(PresetEdge(source=source, target=target, relationship=f"{OWNER}.{source}-input")
            for source, target in [("face", "printer"), ("function", "printer"), ("pack", "packer")])))


def create_plugin():
    return PluginDefinition(PluginDescriptor(id=OWNER, name="卡包工厂", version="0.1.0", plugin_api_version="1.25"), register)
