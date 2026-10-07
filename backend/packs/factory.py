"""Factory operations reuse graph capture, publication checks and Pack installation."""
from datetime import datetime, timezone
import hashlib

from pydantic import Field

from backend.legions.models import LegionCapture, LegionRecord
from backend.legions.presets import preset_record
from backend.packs.content import CreatorRequest, _json, _scan, prepare_export
from backend.packs.archive import MAX_EXPANDED_BYTES
from backend.packs.factory_models import BasketItem, CardRecipe, PackDesign, PackerConfig, PrintedCard
from backend.packs.manifest import Content, Dependencies, PackManifest
from backend.plugins.registry import PLUGIN_API_VERSION
from backend.world.models import CardCreate, CardPatch
from backend import __version__

PREFIX = "oaw.factory."


class CaptureItem(LegionCapture):
    node_ids: list[str] = Field(min_length=1, max_length=101)


def factory_node(services, node_id, kind):
    node = services.world.get_card(node_id)
    services.plugins.node_type(node.type)  # Also enforces enabled ownership.
    if node.type != PREFIX + kind:
        raise ValueError("工厂设备类型不匹配")
    return node


def linked_design(services, node_id, kind):
    edges = services.world.list_edges_to(node_id)
    candidates = [services.world.get_card(edge.source) for edge in edges
                  if edge.relationship == PREFIX + kind + "-input" and edge.direction == "forward"]
    if len(candidates) != 1:
        raise ValueError(f"请连接且只连接一个{ {'face': '卡面设计器', 'function': '功能设计器', 'pack': '卡包设计器'}[kind] }")
    return factory_node(services, candidates[0].id, kind)


async def print_card(services, node_id):
    async with services._node_mutation():
        printer = factory_node(services, node_id, "printer")
        design = PrintedCard(face=linked_design(services, node_id, "face").config,
                             function=linked_design(services, node_id, "function").config)
    return await services._create_card(CardCreate(type=PREFIX + "card", name=design.face.title,
        finish=design.face.finish, config=design.model_dump(mode="json"),
        position={"x": printer.position.x + printer.size.width + 60, "y": printer.position.y}))


def resolve_item(services, item):
    if item.kind == "node":
        node = services.world.get_card(item.id)
        definition = services.plugins.node_type(node.type)
        if not definition.templateable or node.type in {PREFIX + kind for kind in ("pack", "face", "function", "printer", "packer")}:
            raise ValueError("这张卡牌不支持打包，请先印刷设计或选择可保存的卡牌")
        return node
    return preset_record(item.id, services.plugins) if item.kind == "preset" else services.legions.get(item.id)


async def add_item(services, node_id, item: BasketItem):
    packer = factory_node(services, node_id, "packer")
    config = PackerConfig.model_validate(packer.config)
    source = resolve_item(services, item)
    if any(existing.id == item.id and existing.kind == item.kind for existing in config.items):
        return packer
    config.items.append(item.model_copy(update={"name": source.name}))
    config = PackerConfig.model_validate(config.model_dump())
    return await services.update_card(node_id, CardPatch(config=config.model_dump(mode="json"), expected_revision=packer.revision))


def apply_params(design, params):
    result = design.model_copy(deep=True)
    for field in result.function.fields:
        if field.key in params:
            field.default = params[field.key]
    return PrintedCard.model_validate(result.model_dump())


async def prepare_factory_export(services, node_id):
    # One stable graph/resource snapshot for all items, using the same gate as
    # ordinary Legion capture. No saved templates or source cards are modified.
    async with services._portable_state_gate.capture():
        async with services._node_mutation():
            packer = factory_node(services, node_id, "packer")
            config = PackerConfig.model_validate(packer.config)
            design = PackDesign.model_validate(linked_design(services, node_id, "pack").config)
            if not config.items:
                raise ValueError("请先将卡牌或 Legion 加入打包器")
            files, card_paths, legion_paths, issues, dependencies, entries = {}, [], [], [], {}, []
            _scan(config.params, "通用参数", issues)
            _scan(design.creator.model_dump(), "发布信息", issues)
            now = datetime.now(timezone.utc)
            seen = set()
            for item in config.items:
                if (item.kind, item.id) in seen:
                    raise ValueError("打包器中存在重复素材")
                seen.add((item.kind, item.id))
                source = resolve_item(services, item)
                key = hashlib.sha256(f"{item.kind}:{item.id}".encode()).hexdigest()[:16]
                path = f"content/item-{key}.json"
                if (item.kind == "node" and "ui.factory-card.v1" in services.plugins.node_type(source.type).traits
                        and services.state_machines.get(source.id)["definition"] is None):
                    recipe = CardRecipe(id=f"{design.id}.card{key}", design=apply_params(PrintedCard.model_validate(source.config), config.params))
                    _scan(recipe.model_dump(), source.name, issues)
                    _scan({field.key: field.default for field in recipe.design.function.fields}, source.name + ".defaults", issues)
                    files[path] = _json(recipe.model_dump(mode="json"))
                    card_paths.append(path)
                else:
                    if item.kind == "node":
                        blueprint, _ = await services._capture_subgraph_locked(CaptureItem(name=source.name, node_ids=[source.id]))
                        if len(blueprint.nodes) > 101:
                            raise ValueError("单份素材不能超过 101 张卡牌")
                        source = LegionRecord(id=source.id, name=source.name, description="", blueprint=blueprint,
                            created_at=now, updated_at=now, revision=source.revision)
                    source = source.model_copy(deep=True)
                    for node in source.blueprint.nodes:
                        if "ui.factory-card.v1" in services.plugins.node_type(node.type).traits:
                            node.config = apply_params(PrintedCard.model_validate(node.config), config.params).model_dump(mode="json")
                            _scan({field["key"]: field["default"] for field in node.config["function"]["fields"]}, node.name + ".defaults", issues)
                    result, projected = prepare_export(source, CreatorRequest(legion_id=source.id,
                        id=design.id, name=design.name, version=design.version, creator=design.creator,
                        include_state_nodes=[n.key for n in source.blueprint.nodes] if config.include_content else []), services.plugins)
                    from backend.packs.archive import json_object
                    template = json_object(projected["content/legion.json"])
                    template["id"] = f"{design.id}.template{key}"
                    files[path] = _json(template)
                    legion_paths.append(path)
                    issues.extend(result["issues"])
                    dependencies.update({dep["id"]: dep for dep in result["manifest"]["dependencies"]["packs"]})
                entries.append({"name": source.name, "kind": "card" if path in card_paths else "template", "path": path})
                if sum(len(value) for value in files.values()) > MAX_EXPANDED_BYTES - 1024 * 1024:
                    raise ValueError("素材总大小超过卡包容量，请拆成多个卡包")
            manifest = PackManifest(schema_version=2, kind="content", id=design.id, name=design.name, version=design.version,
                compatibility={"oaw": f">={__version__},<1", "plugin_api": PLUGIN_API_VERSION, "frontend_api": 1},
                content=Content(cards=tuple(card_paths), legions=tuple(legion_paths)), creator=design.creator,
                dependencies=Dependencies(packs=tuple(dependencies.values())))
            files["manifest.json"] = _json(manifest.model_dump(mode="json", exclude_none=True))
            creator = design.creator
            files["README.md"] = (f"# {design.name}\n\n{creator.description}\n\n作者: {creator.author}\n\n"
                f"## 准备\n{creator.preparation}\n\n## 示例\n{creator.example}\n\n## 预期结果\n{creator.expected_result}\n\n"
                "## 内容\n" + "\n".join(f"- {entry['name']} ({entry['kind']})" for entry in entries) +
                "\n\n## 安装\n先安装依赖卡包并重启，再从卡牌库安装本 .oawpack 文件并重启。\n" +
                "\n".join(f"- {dep['id']} {dep['version']}" for dep in dependencies.values())).encode("utf-8")
            return {"manifest": manifest.model_dump(mode="json"), "entries": entries, "issues": issues,
                    "can_export": not any(issue["severity"] == "error" for issue in issues)}, files
