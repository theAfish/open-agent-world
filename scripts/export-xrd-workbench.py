"""Export the shipped, clean XRD formation using OAW's content Pack contract."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.legions.presets import preset_record
from backend.packs.content import CreatorRequest, export_archive, prepare_export
from backend.plugins.loader import load_plugin_registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    registry = load_plugin_registry()
    record = preset_record('research.xrd.workbench', registry)
    request = CreatorRequest(legion_id=record.id, id='research.xrdworkbench',
        name='XRD 分析工作台', version='0.2.0', creator={
            'description': record.description,
            'preparation': '先安装匹配版本的 XRD 和 SQL database 插件，配置模型连接与 OAW_XRDfit 环境。谱库由用户自行挂载。',
            'example': '将实验谱拖入谱画布，在检索与比对中挂载谱库，再选择单相或多相流程。',
            'expected_result': '完整工作区、同步谱线和结构、可追溯运行历史；不包含作者数据或计算结果。',
            'accent_color': '#548f86'})
    result, files = prepare_export(record, request, registry)
    if not result['can_export']:
        raise RuntimeError(result['issues'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(export_archive(files))
    print(args.output.resolve())


if __name__ == '__main__':
    main()
