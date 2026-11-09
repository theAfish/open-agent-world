"""Resolve native command evidence without treating the UI's recent cache as a ledger.

Only structured native execution responses count as recovered results. Mentioned
IDs, model text and stdout are never parsed into invented command records.
"""
from collections.abc import Mapping


def command_ids(value):
    if isinstance(value, Mapping):
        found = {value['command_id']} if isinstance(value.get('command_id'), str) else set()
        for child in value.values():
            found.update(command_ids(child))
        return found
    if isinstance(value, list):
        return set().union(*(command_ids(child) for child in value)) if value else set()
    return set()


def execution_results(value, pointer=''):
    """Visit native execution wrappers, not arbitrary measurement dictionaries."""
    if not isinstance(value, Mapping):
        return
    if isinstance(value.get('command_id'), str) and type(value.get('exit_code')) is int:
        yield pointer, value
    for key in ('result', 'execution', 'validation', 'conversion', 'input_check'):
        if isinstance(value.get(key), Mapping):
            yield from execution_results(value[key], pointer + '/' + key)


def resolve_commands(history, tools):
    """Return deduplicated evidence plus explicit gaps; leave original inputs intact."""
    commands, references = {}, {}
    for index, row in enumerate(history):
        if isinstance(row.get('id'), str):
            commands[row['id']] = {**row, 'evidence_source': 'sandbox_history',
                'source_references': [{'path': 'sandbox logs.json', 'pointer': '/' + str(index)}]}
    for tool_index, tool in enumerate(tools):
        if tool.get('state') != 'returned':
            continue
        for identity in command_ids(tool.get('response')):
            references.setdefault(identity, []).append(tool['id'])
        for pointer, result in execution_results(tool.get('response')):
            identity = result['command_id']
            if tool.get('response_complete') is False:
                continue
            reference = {'tool_id': tool['id'], 'path': 'tool calls.json',
                'pointer': '/' + str(tool_index) + '/response' + pointer}
            if identity not in commands:
                # Large output remains solely in the original tool record.
                commands[identity] = {k: result[k] for k in ('argv', 'exit_code', 'started_at',
                    'finished_at', 'duration_seconds', 'sandbox_id', 'timed_out', 'cancelled',
                    'working_directory') if k in result}
                commands[identity].update(id=identity, evidence_source='tool_response',
                    source_references=[reference])
            else:
                row = commands[identity]
                if reference not in row.setdefault('source_references', []):
                    row['source_references'].append(reference)
    gaps = []
    for identity, sources in references.items():
        if identity not in commands:
            gaps.append({'kind': 'command_result_unavailable', 'command_id': identity,
                'tool_ids': sources, 'note': 'Referenced by a native response; no complete execution result was retained.'})
        elif commands[identity]['evidence_source'] == 'tool_response':
            gaps.append({'kind': 'command_history_recovered', 'command_id': identity,
                'tool_ids': sources, 'note': 'Not in bounded Sandbox history; confirmed structured execution remains in the tool log.'})
    return list(commands.values()), gaps
