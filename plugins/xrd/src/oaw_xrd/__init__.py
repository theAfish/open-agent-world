"""PyWPEM runs through the OAW runtime protocol, in its own Python environment."""
from typing import Literal
from pydantic import BaseModel, Field
from open_agent_world.plugin_api import AgentNodeBehavior, NodeTypeDefinition, PluginDescriptor
from .runtime import XRDRuntime

class XRDConfig(BaseModel):
    runtime_provider_id: Literal["research.xrd"] = "research.xrd"
    model: Literal["PyWPEM"] = "PyWPEM"
    max_concurrent_runs: Literal[1] = 1
    inherit_legion_model: Literal[False] = False
    system_instruction: str = "Run the configured PyWPEM fit; completion is not proof of convergence or phase identity."
    intensity_csv: str = Field(default="", max_length=8*1024*1024)
    cif: str = Field(default="", max_length=2*1024*1024)
    iterations: int = Field(default=5, ge=1, le=700)
    wavelength: float = Field(default=1.540593, gt=0.1, le=5)
    low_angle: float = Field(default=20, ge=0, le=170)
    high_angle: float = Field(default=70, gt=0, le=180)
    preoptimize: bool = False
    demo: bool = True

class XRDPlugin:
    descriptor = PluginDescriptor(id="research.xrd",version="0.1.0",plugin_api_version="1.9",
        name="XRD / PyWPEM",description="Candidate-CIF whole-pattern fitting in an isolated local interpreter")
    def register(self, registration):
        registration.register_runtime_provider("research.xrd",XRDRuntime)
        registration.register_node_type(NodeTypeDefinition(id="xrd.analysis",label="XRD 自动拟合",icon="activity",color="#c19875",
            description="PyWPEM whole-pattern fit from intensity CSV and candidate CIF",deck_id="xrd",deck_label="XRD",deck_icon="activity",
            default_name="XRD / PyWPEM",default_size=(340,240),default_status="idle",statuses=frozenset({"idle","running","waiting","error"}),
            config_model=XRDConfig,traits=frozenset({"core.agent","ui.schema-agent.v1"}),
            lifecycle=AgentNodeBehavior(),frontend={"settings":"settings"},
            surfaces={"preview":True,"inspector":True,"workspace":True}))

def create_plugin():
    return XRDPlugin()
