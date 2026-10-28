"""Shipped workbench: no author profile, mounted libraries or run results."""
import json
from importlib.resources import files

from open_agent_world.plugin_api import LegionPresetDefinition

PRESET_ID = 'research.xrd.workbench'


def definition():
    content = json.loads(files(__package__).joinpath('workbench.json').read_text(encoding='utf-8'))
    return LegionPresetDefinition(id=PRESET_ID, name='XRD 分析工作台', revision=2,
        description='导入实验谱、挂载谱库，完成单相拟合或多相筛选与联合复核；内含结构画布、结果讨论及 SQL 运行历史。首次使用请配置模型与计算环境。',
        **content)
