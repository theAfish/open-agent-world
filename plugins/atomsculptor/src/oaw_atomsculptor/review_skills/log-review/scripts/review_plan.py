"""Deterministic report facts and small reviewer annotations over native IDs."""
from copy import deepcopy
import json
from pathlib import PurePosixPath

from oaw_run_evidence import command_ids, execution_results


class ReviewValidationError(ValueError):
    def __init__(self, message, path='/', **details):
        self.issues = [{'path': path, 'message': message, **details}]
        super().__init__(f'{path}: {message}' + ('; ' + json.dumps(details, ensure_ascii=False) if details else ''))


def aliases(attempts):
    result = {}
    for n, attempt in enumerate(attempts, 1):
        for kind, rows, letter in [('tool', attempt['tools'], 'T'), ('model', attempt['models'], 'M'), ('command', attempt['commands'], 'C')]:
            for i, row in enumerate(rows, 1):
                result[f'A{n}:{letter}{i:03d}'] = {'run_id': attempt['run']['run_id'], 'kind': kind, 'id': row['id']}
    return result


def file_candidates(root, attempt, path):
    """Exact native task paths only; a basename match cannot establish ownership."""
    if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or '\\' in path:
        return []
    possible = [root / path]
    bindings = attempt['run'].get('lifecycle', {}).get('sandbox_workspaces', {})
    for sandbox, binding in bindings.items():
        possible.append(root / 'sandbox files' / sandbox / 'workspace' / binding.get('directory', '') / path)
    return sorted({p.relative_to(root).as_posix() for p in possible if p.is_file() and p.resolve().is_relative_to(root)})


def declared_files(tool):
    args = tool.get('effective_arguments') or tool.get('arguments') or {}
    response = tool.get('response') or {}
    response = response.get('result', response) if isinstance(response, dict) else {}
    files = []
    measurement = response.get('measurements') or {}
    if isinstance(measurement, dict):
        files.extend((a.get('path'), 'output') for a in measurement.get('artifacts', []) if isinstance(a, dict))
    for item in args.get('input_artifacts', []) if isinstance(args.get('input_artifacts'), list) else []:
        if isinstance(item, dict):
            files.append((item.get('path'), 'input'))
    if isinstance(args.get('structure_path'), str):
        files.append((args['structure_path'], 'unchanged' if tool['name'] in {'validate_modelling_artifact', 'run_delivery_step', 'inspect_atom_structure'} else 'input'))
    if tool['name'] == 'run_delivery_step':
        for key in ('document_file', 'file'):
            if isinstance(response.get(key), str):
                files.append((response[key], 'unchanged'))
    return list(dict.fromkeys((path, relation) for path, relation in files if path))


def native_plan(root, attempts, operation_rows):
    """A graph of executions/structure actions; every other call stays in the ledger."""
    by_tool = {(a['run']['run_id'], t['id']): (a, t) for a in attempts for t in a['tools']}
    selected, seen_commands = [], set()
    special = {'validate_modelling_artifact', 'run_delivery_step', 'replace_atom_structure',
        'import_atom_structure_file', 'build_supercell'}
    for row in operation_rows:
        attempt, tool = by_tool[row['run_id'], row['tool_id']]
        executions = list(execution_results(tool.get('response')))
        fresh = [value['command_id'] for _, value in executions if (row['run_id'], value['command_id']) not in seen_commands]
        if tool['name'] not in special and not fresh:
            continue
        # Rejected declarations remain visible in the complete ledger, without
        # claiming a geometry change or a dispatched process.
        response = tool.get('response') or {}
        response = response if isinstance(response, dict) else {}
        if response.get('error') and not fresh:
            continue
        seen_commands.update((row['run_id'], value) for value in fresh)
        selected.append((row, attempt, tool, fresh))
    def file_identity(attempt, path):
        paths = file_candidates(root, attempt, path)
        if len(paths) == 1:
            return ('saved_file', paths[0])
        origin = attempt['run'].get('lifecycle', {}).get('task_workspace', {}).get('origin_run_id') or attempt['run']['run_id']
        return (origin, path)

    last_writer = {}
    for index, (_, attempt, tool, _) in enumerate(selected):
        for path, relation in declared_files(tool):
            if relation == 'output':
                last_writer[file_identity(attempt, path)] = index
    nodes = []
    for index, (row, attempt, tool, fresh) in enumerate(selected):
        node = {'id': row['short_id'].replace(':', '-'), 'title': row['action'],
            'run_id': row['run_id'], 'tool_id': row['tool_id'], 'parents': [],
            'outcome': '; '.join(row['observed']), 'structures': [], 'attachments': [],
            'started_at': row.get('started_at'),
            'step': row['short_id'], 'kind': 'validation' if tool['name'] == 'validate_modelling_artifact'
                else 'delivery' if tool['name'] == 'run_delivery_step' else 'execution'}
        response = tool.get('response') or {}
        response = response if isinstance(response, dict) else {}
        saved_step = response.get('step') or {}
        if isinstance(saved_step, dict):
            node.update({key: saved_step[key] for key in ('source_version', 'script_path', 'execution_scope') if key in saved_step})
            if saved_step.get('source_version') is not None:
                node['title'] += ' · v' + str(saved_step['source_version'])
        if len(fresh) == 1 and fresh[0] in command_ids(tool.get('response')):
            node['command_id'] = fresh[0]
        for path, relation in declared_files(tool):
            paths = file_candidates(root, attempt, path)
            newer = last_writer.get(file_identity(attempt, path), index) > index
            if len(paths) == 1 and not newer:
                item = {'path': paths[0], 'provenance': 'collection_snapshot', 'relation': relation,
                    'note': 'Exact path referenced by this native operation. Collected later; the original bytes at this step are not independently preserved.'}
            else:
                item = {'provenance': 'missing', 'relation': relation,
                    'note': f'{path}: ' + ('a later recorded producer used this path; do not substitute its snapshot.' if newer
                        else 'no unique saved file at this native task path.')}
            node['structures'].append(item)
        if not node['structures']:
            node['structures'].append({'provenance': 'missing',
                'note': 'No step-specific structure file was retained or declared. This node is an execution record, not proof of a geometry edit.'})
        nodes.append(node)
    # Keep actual commands that lack an accepted tool association; never merge
    # distinct executions merely because their resulting files are identical.
    for n, attempt in enumerate(attempts, 1):
        for i, command in enumerate(attempt['commands'], 1):
            if (attempt['run']['run_id'], command['id']) in seen_commands:
                continue
            references = []
            argv = command.get('argv', [])
            if isinstance(argv, list):
                # These are the existing Skill CLI's literal file selectors,
                # not filenames guessed from arbitrary program source/stdout.
                options = {argv[j]: argv[j+1] for j in range(len(argv)-1)
                    if argv[j] in {'--folder', '--file-name', '--output-name', '--structure-path'}}
                for flag in ('--file-name', '--structure-path', '--output-name'):
                    if flag not in options:
                        continue
                    path = str(PurePosixPath(options.get('--folder', '.')) / options[flag]) if flag == '--file-name' else options[flag]
                    paths = file_candidates(root, attempt, path)
                    if len(paths) == 1:
                        references.append({'path': paths[0], 'provenance': 'collection_snapshot',
                            'relation': 'unchanged' if flag != '--output-name' else 'output',
                            'note': 'Referenced by literal native command argv; current collected bytes, not an independently preserved execution-time version.'})
            nodes.append({'id': f'A{n}-C{i:03d}', 'step': f'A{n}:C{i:03d}', 'title': '原生命令记录',
                'run_id': attempt['run']['run_id'], 'command_id': command['id'], 'parents': [],
                'structures': references, 'attachments': [], 'kind': 'execution',
                'started_at': command.get('started_at'),
                'outcome': 'No recorded tool association; exit=' + str(command.get('exit_code', 'unknown'))})
    # Preserve start order where available. A container tool may enclose its
    # inner command; the sequence arrow makes no claim of data dependence.
    nodes.sort(key=lambda node: node.get('started_at') or '')
    goals = []
    for attempt in attempts:
        prompt = attempt['directory'] / 'prompt.txt'
        if prompt.is_file():
            text = prompt.read_text(encoding='utf-8')
            goals.append(text[:1200] + ('…（原文另存）' if len(text) > 1200 else ''))
    return {'title': '结构修改与交付报告', 'summary': '原始任务（原文节选）：\n' + '\n\n'.join(goals) if goals else '原始任务文本未保留；以原生执行证据为准。', 'nodes': nodes,
        'operations': [], 'sequence_edges': True}


NOTE_FIELDS = {'purpose', 'finding', 'follow_up', 'basis', 'evidence', 'node'}
NODE_FIELDS = {'title', 'outcome', 'parents', 'structures', 'attachments'}


def compile_notes(root, attempts, operation_rows, notes):
    if notes.get('format') != 'oaw.review-notes.v1':
        raise ReviewValidationError('Expected oaw.review-notes.v1', '/format')
    native = native_plan(root, attempts, operation_rows)
    identities = aliases(attempts)
    nodes = {node['step']: node for node in native['nodes']}
    updates = notes.get('steps', {})
    if not isinstance(updates, dict):
        raise ReviewValidationError('steps must be an object keyed by stable short ID', '/steps')
    for step, note in updates.items():
        pointer = '/steps/' + str(step)
        if step not in identities or identities[step]['kind'] not in {'tool', 'command'} or not isinstance(note, dict):
            raise ReviewValidationError('Unknown operation step', pointer, available_steps=[r['short_id'] for r in operation_rows])
        if set(note) - NOTE_FIELDS:
            raise ReviewValidationError('Only reviewer explanations and node annotations are editable', pointer,
                unexpected=sorted(set(note) - NOTE_FIELDS))
        evidence = note.get('evidence', [step])
        if not isinstance(evidence, list) or any(not isinstance(ref, str) or ref not in identities for ref in evidence):
            raise ReviewValidationError('Use evidence short IDs from the index', pointer + '/evidence', available_evidence=list(identities))
        explanation = {**{k: note[k] for k in ('purpose', 'finding', 'follow_up', 'basis') if k in note},
            'evidence': [identities[ref] for ref in evidence]}
        if any(not isinstance(explanation.get(key, ''), str) for key in ('purpose', 'finding', 'follow_up')):
            raise ReviewValidationError('Explanation fields must be text', pointer)
        if explanation.get('basis', 'unknown') not in {'stated', 'inferred', 'unknown'}:
            raise ReviewValidationError('basis must be stated, inferred or unknown', pointer + '/basis')
        if explanation.get('basis') == 'stated' and not explanation['evidence']:
            raise ReviewValidationError('A stated explanation needs its native evidence reference', pointer + '/evidence')
        if identities[step]['kind'] == 'tool':
            native['operations'].append({**explanation, 'run_id': identities[step]['run_id'], 'tool_id': identities[step]['id']})
        elif step in nodes and not nodes[step].get('tool_id'):
            nodes[step]['annotation'] = explanation
        else:
            raise ReviewValidationError('This command is already represented by its tool step; annotate that step', pointer)
        if 'node' in note:
            change = note['node']
            if step not in nodes or not isinstance(change, dict) or set(change) - NODE_FIELDS:
                raise ReviewValidationError('Only existing modification nodes can be annotated', pointer + '/node', modification_steps=list(nodes))
            change = deepcopy(change)
            if 'parents' in change:
                if not isinstance(change['parents'], list) or any(parent not in nodes for parent in change['parents']):
                    raise ReviewValidationError('parents must reference modification step short IDs', pointer + '/node/parents')
                change['parents'] = [nodes[parent]['id'] for parent in change['parents']]
            nodes[step].update(change)
    for key in ('title', 'summary'):
        if not isinstance(notes.get(key, ''), str):
            raise ReviewValidationError('Expected text', '/' + key)
        if notes.get(key):
            native[key] = notes[key]
    return native
