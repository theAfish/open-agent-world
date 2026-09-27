"""PyWPEM runs through the OAW runtime protocol, in its own Python environment."""
from open_agent_world.plugin_api import PackDefinition
from typing import Literal
from pydantic import BaseModel, Field
from open_agent_world.plugin_api import AgentNodeBehavior, NodeTypeDefinition, PluginDescriptor
from .templates import remap_config, XRDTemplateHandler
from .runtime import XRDRuntime
from .history import actions as history_actions
from .sql_history import HistoryAgentBehavior
from .multiphase_object import register_harness
from .results_context import register_results_context
from .documents import InputDocument, import_input, associate
from open_agent_world.plugin_api import (NodeDocumentDefinition, NodeDocumentAction, NodeDocumentDownload,
    CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition)
import base64
import sqlite3
from .library import inspect_library
from .structures import MatchDocument, prepare_cod_structure, apply_cod_structure, download_structure
from open_agent_world.plugin_api import ResourceValidationError

class LibraryDocument(BaseModel):
    kind: Literal['library'] = 'library'
    path: str = ''
    filename: str = ''
    sha256: str = ''
    size_bytes: int = 0
    mtime_ns: int = 0
    count: int = 0
    metadata: dict = Field(default_factory=dict)
    points: list = Field(default_factory=list)
    peaks: list = Field(default_factory=list)
    slots: list[dict | None] = Field(default_factory=list)

def configure_library(value, args):
    try:
        slot = args.get('slot', 0)
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 6:
            raise ValueError('槽位必须为 1–6')
        mounted = inspect_library(args.get('path', ''))
        slots = list(value.get('slots') or ([{k:v for k,v in value.items() if k != 'slots'}] if value.get('path') else []))
        slots += [None] * (6 - len(slots))
        if any(d and i != slot and d.get('path') == mounted['path'] for i,d in enumerate(slots)):
            raise ValueError('这个数据库已经挂载在其他槽位')
        slots[slot] = mounted
        return {**next(d for d in slots if d), 'slots': slots}
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise ResourceValidationError(str(exc)) from exc

class MatchWorkspaceDocument(LibraryDocument, MatchDocument):
    pass

class XRDConfig(BaseModel):
    status: Literal["idle", "running", "waiting", "error"] = "idle"
    mode: Literal["fit", "match"] = "fit"
    connected_inputs: bool = False
    cif_node_id: str = ""
    tolerance_deg: float = Field(default=.15, gt=0, le=2)
    prominence_fraction: float = Field(default=.03, gt=0, le=.5)
    min_peak_distance: float = Field(default=.1, gt=0, le=2)
    smoothing_deg: float = Field(default=.03, gt=0, le=.5)
    reference_min_intensity: float = Field(default=5, ge=0, le=100)
    library_top_n: int = Field(default=20, ge=1, le=100)
    library_elements: str = Field(default='', max_length=300)
    library_engine: Literal['qualx', 'native'] = 'qualx'
    workflow_stage: Literal['search', 'preopt', 'fit'] = 'search'
    workflow_started_at_ms: int = Field(default=0, ge=0)
    workflow_match_run_id: str = Field(default='', max_length=128)
    workflow_preopt_run_id: str = Field(default='', max_length=128)
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=10)
    preopt_max_nfev: int = Field(default=120, ge=1, le=2000)
    preopt_coordinate_window: float = Field(default=.01, gt=0, le=.1)
    preopt_cell_window: float = Field(default=.35, gt=0, le=.5)
    runtime_provider_id: Literal["research.xrd"] = "research.xrd"
    model: Literal["OAW_XRDfit", "PyWPEM", "Peak matching"] = "OAW_XRDfit"
    max_concurrent_runs: Literal[1] = 1
    inherit_legion_model: Literal[False] = False
    system_instruction: str = "Run the configured OAW_XRDfit fit; completion is not proof of convergence or phase identity."
    intensity_csv: str = Field(default="", max_length=8*1024*1024)
    cif: str = Field(default="", max_length=2*1024*1024)
    iterations: int = Field(default=5, ge=1, le=700)
    wavelength: float = Field(default=1.540593, gt=0.1, le=5)
    low_angle: float = Field(default=20, ge=0, le=170)
    high_angle: float = Field(default=70, gt=0, le=180)
    preoptimize: bool = False
    demo: bool = False

class MatchConfig(XRDConfig):
    model: Literal["Peak matching", "OAW_XRDfit", "PyWPEM"] = "Peak matching"
    system_instruction: str = "Screen connected experimental peaks against reference cards; matching does not establish phase identity or purity."
    mode: Literal["match"] = "match"
    connected_inputs: Literal[True] = True
    demo: Literal[False] = False

class InputConfig(BaseModel):
    pass

class CanvasConfig(BaseModel):
    source_node_id: str = ''

class XRDPlugin:
    descriptor = PluginDescriptor(id="research.xrd",version="0.3.0",plugin_api_version="1.23",
        name="XRD / OAW_XRDfit",description="Reference-card screening and candidate-CIF whole-pattern fitting")
    async def read_input(self, context, capability, arguments):
        document = await context.node_document_action(capability, "read", arguments)
        return {"node_id": capability.target_id, "revision": document["revision"], "value": document["value"]}

    def register(self, registration):
        registration.register_runtime_provider("research.xrd",XRDRuntime)
        register_harness(registration)
        register_results_context(registration)
        registration.register_relationship(RelationshipDefinition(
            id='xrd.history-store', label='XRD 运行数据库', short_label='运行记录',
            description='结构化保存运行、Jev 决策、评估、Agent 报告和结果文件；只连接一个 SQL database',
            source_types=frozenset({'xrd.match', 'xrd.analysis'}), target_types=frozenset({'data.sqlite'})))
        for kind, label, icon in [('spectrum', 'XRD 谱画布', 'xrd-spectrum'), ('structure', 'XRD 结构画布', 'atom')]:
            registration.register_node_type(NodeTypeDefinition(id=f'xrd.{kind}-canvas', label=label, icon=icon, color='#c19875',
                description='由检索与比对时间轴同步控制', deck_id='xrd', deck_label='XRD', deck_icon='xrd-spectrum',
                default_name=label, default_size=(600,420), default_status='available', statuses=frozenset({'available'}),
                config_model=CanvasConfig, templateable=True, template_remap_config=remap_config, traits=frozenset({'xrd.frame-canvas', *({'xrd.readable'} if kind == 'spectrum' else set())}),
                document=NodeDocumentDefinition(model=InputDocument, initial_value={'kind':'pattern'}, max_size_bytes=40*1024*1024, capture=lambda v: {'kind':'pattern'},
                    actions={'import':NodeDocumentAction(lambda v,a:import_input({**v,'kind':'pattern'},a)), 'read':NodeDocumentAction(lambda v,a:{**v,'kind':'pattern'},read_only=True,capability_kind='xrd.read')},
                    downloads={'source':lambda v:NodeDocumentDownload(v['filename'] or 'input.txt',base64.b64decode(v['source_base64']))}) if kind == 'spectrum' else NodeDocumentDefinition(model=InputDocument, initial_value={'kind':'canvas'}, capture=lambda v:{'kind':'canvas'}, actions={'read':NodeDocumentAction(lambda v,a:v,read_only=True,capability_kind='xrd.read')}),
                frontend={'body':'frame-canvas','workspace':'frame-canvas','preview':'frame-canvas'},
                surfaces={'preview':True,'inspector':True,'workspace':True}))
        registration.register_relationship(RelationshipDefinition(id='xrd.frames', templateable=True, capabilities=(CapabilityGrantDefinition(kind='xrd.read'),), label='同步帧', short_label='同步',
            description='检索与比对时间轴控制画布', source_traits=frozenset({'core.agent'}), target_traits=frozenset({'xrd.frame-canvas'})))
        registration.register_node_type(NodeTypeDefinition(id='xrd.library', label='XRD 全库挂载', icon='hard-drive', color='#c19875',
            description='本地 QualX / POW_COD 全库检索；ICDD 格式尚未支持', deck_id='xrd', deck_label='XRD', deck_icon='activity',
            default_name='全库挂载', default_size=(400, 280), default_status='available', statuses=frozenset({'available'}),
            user_creatable=False, config_model=InputConfig, traits=frozenset({'xrd.readable', 'xrd.library'}), templateable=True,
            frontend={'body':'library','workspace':'library','preview':'library'}, surfaces={'preview':True,'inspector':True,'workspace':True},
            document=NodeDocumentDefinition(model=LibraryDocument, initial_value={'kind':'library'}, capture=lambda v: {'kind':'library'},
                actions={'configure':NodeDocumentAction(configure_library),
                    'read':NodeDocumentAction(lambda v,a:v,read_only=True,capability_kind='xrd.read')},
                summarize=lambda v:{'filename':v['filename'],'count':v['count'],'sha256':v['sha256']})))
        registration.register_node_type(NodeTypeDefinition(id="xrd.analysis",label="XRD 自动拟合",icon="xrd-spectrum",color="#c19875",
            description="OAW_XRDfit whole-pattern fit from intensity CSV and candidate CIF",deck_id="xrd",deck_label="XRD",deck_icon="xrd-spectrum",
            default_name="XRD / OAW_XRDfit",default_size=(340,240),default_status="idle",statuses=frozenset({"idle","running","waiting","error"}),
            templateable=True, template_status="idle", template_handler=XRDTemplateHandler(), template_remap_config=remap_config, config_model=XRDConfig,traits=frozenset({"core.agent","ui.schema-agent.v1","ui.direct-run.v1"}),
            lifecycle=HistoryAgentBehavior(),resource_actions=history_actions(),frontend={"settings":"settings"},
            surfaces={"preview":True,"inspector":True,"workspace":True}))
        registration.register_node_type(NodeTypeDefinition(id="xrd.match", label="XRD 检索与比对", icon="xrd-spectrum", color="#c19875",
            description="连接标准卡片进行比对，连接谱库进行全库检索；也可同时连接", deck_id="xrd", deck_label="XRD", deck_icon="xrd-spectrum",
            default_name="XRD / 检索与比对", default_size=(440, 380), default_status="idle",
            statuses=frozenset({"idle", "running", "waiting", "error"}), config_model=MatchConfig, templateable=True, template_status="idle", template_handler=XRDTemplateHandler(), template_remap_config=remap_config,
            traits=frozenset({"core.agent", "ui.schema-agent.v1", "ui.direct-run.v1"}), lifecycle=HistoryAgentBehavior(),
            resource_actions=history_actions(), frontend={"settings": "settings"}, surfaces={"preview": True, "inspector": True, "workspace": True},
            document=NodeDocumentDefinition(model=MatchWorkspaceDocument, initial_value={'kind':'library','structure': None}, capture=lambda v: {'kind':'library','structure': None}, max_size_bytes=4*1024*1024,
                actions={'configure':NodeDocumentAction(lambda v,a:{**configure_library(v,a), 'structure':v.get('structure')}), 'fetch_cod': NodeDocumentAction(apply_cod_structure, prepare=prepare_cod_structure)},
                downloads={'structure': download_structure},
                summarize=lambda v: {'cod_id': v['structure']['cod_id']} if v.get('structure') else {})))
        for kind, label, icon in (("pattern", "XRD 实验谱", "activity"), ("reference", "XRD 标准卡片", "file-text"), ("cif", "XRD 候选 CIF", "box")):
            registration.register_node_type(NodeTypeDefinition(id=f"xrd.{kind}", label=label, icon="xrd-spectrum", color="#c19875",
                description=label, deck_id="xrd", deck_label="XRD", deck_icon="xrd-spectrum", default_name=label,
                default_size=(340, 280), default_status="available", statuses=frozenset({"available"}), config_model=InputConfig,
                user_creatable=kind != "pattern", traits=frozenset({"xrd.readable", f"xrd.{kind}"}), templateable=True,
                frontend={"body": "input", "workspace": "input", "preview": "input"},
                surfaces={"preview": True, "inspector": True, "workspace": True},
                document=NodeDocumentDefinition(model=InputDocument, initial_value={"kind": kind}, capture=lambda v: {"kind": v["kind"]}, max_size_bytes=40*1024*1024,
                    actions={"import": NodeDocumentAction(import_input), "associate": NodeDocumentAction(associate),
                             "read": NodeDocumentAction(lambda v, a: v, read_only=True, capability_kind="xrd.read")},
                    summarize=lambda v: {"filename": v["filename"], "sha256": v["sha256"], "points": len(v["points"]), "peaks": len(v["peaks"])},
                    remap_references=lambda v, ids: {**v, "reference_node_id": ids.get(v.get("reference_node_id", ""), "")},
                    downloads={"source": lambda v: NodeDocumentDownload(v["filename"] or "input.txt", base64.b64decode(v["source_base64"]))})))
        registration.register_capability(CapabilityDefinition(kind="xrd.read", tool_name="read_xrd_input",
            description="Read a connected XRD spectrum, reference peak table or candidate CIF and its source hash.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False}), self.read_input)
        registration.register_relationship(RelationshipDefinition(id="xrd.input", label="XRD 输入", short_label="输入",
            description="Grant this algorithm read access to the XRD input object", source_traits=frozenset({"core.agent"}),
            target_traits=frozenset({"xrd.readable"}), capabilities=(CapabilityGrantDefinition(kind="xrd.read"),), templateable=True))
        registration.register_pack(PackDefinition(id='research.xrd.default', name='XRD analysis',
            description='Whole-pattern diffraction fitting.', cards=tuple(registration.nodes)))

def create_plugin():
    return XRDPlugin()
