import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot } from '../../src/plugins/executionSources';

const edge = (source: string, target: string, kind: ExecutionEdge['kind'] = 'control'): ExecutionEdge => ({ id: `${source}:${target}:${kind}`, source, target, kind });
export function graphFixture(scenario: string): ExecutionSnapshot {
  const snapshot: ExecutionSnapshot = { title: 'Structure search · research session', phase: 'idle', history_id: scenario, revision: 1, nodes: [], edges: [] };
  if (scenario === 'empty') return snapshot;
  if (scenario === 'details') {
    snapshot.title = 'Workspace cleanup · preserve and verify';
    snapshot.nodes = [
      { id: 'inspect', label: 'Inspect workspace and confirm deletion targets', kind: 'agent', status: 'success', actor_id: 'planner', actor_label: 'Planner', sequence: 1 },
      { id: 'preserve', label: 'Before deleting, archive the only two delete targets', kind: 'agent', status: 'success', actor_id: 'executor', actor_label: 'Executor n2_preserve', sequence: 2,
        started_at: '2026-10-15T02:45:35Z', finished_at: '2026-10-15T02:45:57Z', execution_id: 'agent:execution_1', attempt: 1,
        result: 'Archived the two remaining delete targets before deletion. The retained manifest covers both dummy_graph_test and test_job. Nothing is lost; deletion can proceed.',
        detail: 'publish_artifact returned state=ready. inspect_artifacts independently confirmed the retained version.',
        artifact_refs: ['oaw:artifact:fce8d87b-046a-4b7b-a5ad-e74773df5f8d:version:f2177302-d45a-4337-bcda-a94d15357051'] },
      { id: 'manifest', label: 'Write /workspace/cleanup_manifest.json', kind: 'agent', status: 'success', actor_id: 'writer', actor_label: 'Executor n3_manifest', sequence: 3 },
      { id: 'verify', label: 'Verify retained artifacts before deletion', kind: 'agent', status: 'waiting', actor_id: 'reviewer', actor_label: 'Reviewer', sequence: 4 },
    ];
    snapshot.edges = [edge('inspect', 'preserve'), edge('outside-retention', 'preserve', 'data'), edge('preserve', 'manifest', 'data'), edge('preserve', 'verify')];
    return snapshot;
  }
  if (scenario === 'compact') {
    snapshot.title = 'Parallel work · compact rails';
    const tasks: [string, string, string, string, number][] = [
      ['start', 'Plan parallel checks', 'Mira', 'main', 4],
      ['quick', 'Check reference data', 'Nova', 'quick', 5],
      ['deep', 'Run detailed analysis', 'Atlas', 'deep', 3],
      ['validate', 'Validate analysis results', 'Atlas', 'deep', 7],
      ['progress', 'Review current progress', 'Mira', 'main', 5],
      ['accept-quick', 'Accept reference check', 'Mira', 'main', 6],
      ['prepare', 'Prepare combined report', 'Mira', 'main', 7],
      ['accept-deep', 'Accept detailed analysis', 'Mira', 'main', 8],
      ['next', 'Launch follow-up checks', 'Mira', 'main', 10],
      ['local', 'Check local constraints', 'Nova', 'local', 13],
      ['monitor', 'Review follow-up progress', 'Mira', 'main', 12],
      ['remote', 'Run extended simulation', 'Atlas', 'remote', 13],
    ];
    snapshot.nodes = tasks.map(([id, label, actor, stream, finish], minute) => ({ id, label, kind: 'agent', status: 'success',
      actor_id: actor, actor_label: actor, workstream_id: stream, sequence: minute,
      started_at: new Date(Date.UTC(2026, 9, 15, 9, minute)).toISOString(),
      finished_at: new Date(Date.UTC(2026, 9, 15, 9, finish)).toISOString() }));
    snapshot.edges = [edge('start', 'quick', 'fork'), edge('start', 'deep', 'fork'), edge('start', 'progress'),
      edge('deep', 'validate'), edge('quick', 'accept-quick', 'merge'), edge('progress', 'accept-quick'),
      edge('accept-quick', 'prepare'), edge('prepare', 'accept-deep'), edge('validate', 'accept-deep', 'merge'),
      edge('accept-deep', 'next'), edge('accept-deep', 'remote', 'fork'), edge('next', 'local', 'fork'), edge('next', 'monitor')];
    return snapshot;
  }
  if (scenario === 'braid') {
    snapshot.title = 'Materials study · three executions';
    snapshot.phase = 'running';
    const event = (id: string, label: string, actor: string, stream: string, minute: number, finish: number | null, status = 'success', execution = 'execution-1'): ExecutionNode => ({
      id, label, actor_id: actor, actor_label: actor, workstream_id: stream,
      execution_id: execution, kind: 'agent', role: 'step', status, sequence: minute,
      started_at: new Date(Date.UTC(2026, 9, 15, 9, minute)).toISOString(),
      finished_at: finish === null ? null : new Date(Date.UTC(2026, 9, 15, 9, finish)).toISOString(),
    });
    snapshot.nodes = [
      event('plan', 'Plan the structure study', 'Mira', 'planning', 0, 1),
      event('build', 'Build candidate structures', 'Atlas', 'structures', 1, 5),
      event('scan', 'Scan reference literature', 'Nova', 'references', 2, 4),
      event('baseline', 'Calculate baseline energy', 'Atlas', 'baseline', 3, 7),
      event('handoff', 'Validate reference data', 'Iris', 'references', 4, 6),
      event('failed', 'Relax candidate geometry', 'Atlas', 'structures', 5, 6, 'failed'),
      { ...event('retry', 'Retry with stable parameters', 'Nova', 'structures', 6, 8), attempt: 2, retry_of: 'failed' },
      event('combine', 'Compare all three results', 'Mira', 'synthesis', 8, 9),
      event('independent', 'Audit compute quota', 'Iris', 'quota', 9, 12),
      event('apply', 'Accept validated structures', 'Mira', 'synthesis', 10, 11),
      { ...event('replan', 'Plan transport calculations', 'Mira', 'planning', 11, 12, 'success', 'execution-2'), replan_of: 'failed' },
      event('transport', 'Compute transport properties', 'Atlas', 'transport', 12, null, 'running', 'execution-2'),
      event('remote', 'Wait for remote simulation', 'Nova', 'remote', 13, null, 'waiting', 'execution-2'),
      event('review', 'Request peer review', 'Iris', 'review', 14, null, 'awaiting_peer_review', 'execution-2'),
      ...Array.from({ length: 5 }, (_, i) => event('archive-' + i, 'Archive sample ' + (i + 1), 'Iris', 'archive', 15 + i, 16 + i, 'success', 'execution-2')),
      event('report', 'Reconcile cross-execution results', 'Mira', 'synthesis', 20, null, 'needs_replanning', 'execution-3'),
    ];
    snapshot.edges = [
      edge('plan', 'build', 'fork'), edge('plan', 'scan', 'fork'), edge('plan', 'baseline', 'fork'),
      edge('scan', 'handoff'), edge('build', 'failed'), edge('failed', 'retry', 'retry'),
      edge('retry', 'combine'), edge('handoff', 'combine'), edge('baseline', 'combine'),
      { ...edge('retry', 'combine', 'data'), artifact_ref: 'oaw:artifact:structures:v2', evidence: 'explicit_input_binding' },
      edge('combine', 'apply', 'merge'), edge('failed', 'replan', 'replan'),
      edge('replan', 'transport', 'fork'), edge('replan', 'remote', 'fork'),
      edge('apply', 'review', 'data'), edge('retry', 'report', 'data'), edge('apply', 'report', 'data'),
      edge('remote', 'report'), edge('outside-retention', 'report', 'data'),
    ];
    return snapshot;
  }
  if (scenario === 'independent') {
    snapshot.title = 'Three independent executions';
    snapshot.nodes = ['Prepare structure', 'Check symmetry', 'Export results'].map((label, i) => ({ id: `n${i}`, label, kind: 'agent', role: 'execution', status: i === 2 ? 'running' : 'success', sequence: i }));
    return snapshot;
  }
  if (scenario === 'parallel') {
    snapshot.title = 'Crystal parser · repair and verify';
    const operations = [
      ['project', 'Review parser failure', 'Mira', 'plan'],
      ['repair', 'Update structure parser', 'Atlas', 'edit'],
      ['verify', 'Add regression cases', 'Nova', 'edit'],
      ['repair', 'Handle missing coordinates', 'Atlas', 'edit'],
      ['verify', 'Run parser tests', 'Nova', 'run'],
      ['repair', 'Fix empty structure input', 'Atlas', 'edit'],
      ['verify', 'Verify the repair', 'Nova', 'run'],
      ['project', 'Apply verified changes', 'Mira', 'merge'],
    ];
    snapshot.nodes = operations.map(([stream, label, actor, operation], i) => ({ id: `E${i + 1}`, label, kind: 'agent',
      role: 'step', actor_id: actor, actor_label: actor, workstream_id: stream, operation,
      status: i === 4 ? 'failed' : 'success', sequence: i,
      started_at: new Date(Date.UTC(2026, 9, 13, 9, 10 + i)).toISOString(),
      detail: i === 4 ? 'Empty structure input failed validation. The failed attempt remains in the project history.' : '',
    }));
    snapshot.edges = [edge('E1','E2','fork'), edge('E1','E3','fork'), edge('E4','E5','data'),
      edge('E5','E6','data'), edge('E5','E7','retry'), edge('E6','E7','data'), edge('E6','E8','merge'), edge('E7','E8','merge')];
    return snapshot;
  }
  const count = scenario === 'large' ? 120 : 20;
  const nodes: ExecutionNode[] = [];
  for (let n = 1; n <= count; n++) {
    const id = `E${String(n).padStart(2, '0')}`;
    nodes.push({ id, label: id, kind: 'agent', role: 'execution', execution_id: id, sequence: n * 10,
      actor_id: scenario === 'large' ? `executor-${n % 3}` : undefined, actor_label: scenario === 'large' ? ['Atlas', 'Nova', 'Mira'][n % 3] : undefined,
      workstream_id: scenario === 'large' ? id : undefined, status: n === count ? 'running' : 'success' });
    if (scenario === 'large') for (let step = 1; step <= 9; step++) {
      nodes.push({ id: `${id}:s${step}`, label: ['Prepare inputs', 'Relax geometry', 'Check symmetry', 'Evaluate energy', 'Compare structures', 'Compute transport', 'Validate outputs', 'Review results', 'Publish result'][step - 1], kind: 'plan', role: 'step', parent_id: id, execution_id: id,
        actor_id: `executor-${n % 3}`, actor_label: ['Atlas', 'Nova', 'Mira'][n % 3], workstream_id: id,
        sequence: n * 10 + step, status: n === count ? step < 6 ? 'success' : step === 9 ? 'waiting' : 'running' : 'success' });
      if (step > 1) snapshot.edges!.push(edge(`${id}:s${step === 5 ? 2 : step - 1}`, `${id}:s${step}`));
    }
  }
  snapshot.nodes = nodes;
  if (scenario === 'sparse') snapshot.edges = [1,9,17].map(n => edge(`E${String(n).padStart(2, '0')}`, 'E20', 'data'));
  else {
    for (let n = 1; n < 120; n++) snapshot.edges!.push(edge(`E${String(n).padStart(2, '0')}:s9`, `E${String(n + 1).padStart(2, '0')}:s1`));
    snapshot.edges!.push(...[1,9,17].map(n => edge(`E${String(n).padStart(2, '0')}:s9`, 'E120:s9', 'data')));
  }
  return snapshot;
}
