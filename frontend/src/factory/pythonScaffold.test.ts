import { expect, it } from 'vitest';
import { createPythonStarter } from './pythonScaffold';
import type { PackProject } from './projectTypes';

const project: PackProject = {
  id: 'project', path: '/example', name: 'Example', distribution_id: 'local.example', version: '0.1.0', revision: 'hash',
  files: [{ path: 'pyproject.toml', size_bytes: 12, sha256: 'hash', editable: true }], diagnostics: [],
  spec: { owner: 'local.example', packs: ['local.example'], entrypoints: {} },
  cards: [{ id: 'local.example.card', label: 'Example card', kind: 'declarative', design_path: 'designs/card.json',
    design: { face: {} as never, action: 'calculate', result_key: 'answer', surfaces: ['body'], function: {
      fields: [{ key: 'count', label: 'Count', type: 'number', default: 3, required: true }],
      operation: 'sum', template: '', separator: '\n', button_bindings: [{ mode: 'body' as never, element_id: 'run', logic_id: 'calculate' }],
    } },
  }],
};

it('stages only a source starter and explicit setup guide while preserving the current declarative project', () => {
  const before = structuredClone(project);
  const result = createPythonStarter(project);
  expect(Object.keys(result.files)).toEqual(['src/oaw_custom/__init__.py', 'PYTHON_SETUP.md']);
  const source = result.files[result.primaryPath];
  expect(source).toContain('card_id = "local.example.card"');
  expect(source).toContain('"calculate": NodeResourceAction(run_card_1)');
  expect(source).toContain('# return {"answer": your_result}');
  expect(source).toContain('id="local.example", name="Example", cards=tuple(registration.nodes)');
  expect(result.files['PYTHON_SETUP.md']).toContain('Your current no-code behavior is still active.');
  expect(project).toEqual(before);
});

it('refuses existing source, pending starter files, and an already active Python entrypoint', () => {
  expect(() => createPythonStarter(project, ['src/oaw_custom/helpers.py'])).toThrow('already exist');
  expect(() => createPythonStarter({ ...project, files: [...project.files, { path: 'PYTHON_SETUP.md', size_bytes: 20, sha256: 'hash', editable: true }] })).toThrow('already exist');
  expect(() => createPythonStarter({ ...project, spec: { ...project.spec, entrypoints: { 'local.example': 'existing:plugin' } } })).toThrow('existing Python implementation');
});
