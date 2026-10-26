import type { PackProject } from './projectTypes';

export interface PythonStarter { primaryPath: string; files: Record<string, string> }

/** Stage ordinary source files. Connecting the entrypoint is an explicit author decision. */
export function createPythonStarter(project: PackProject, pendingPaths: readonly string[] = []): PythonStarter {
  if (!project.cards.length || project.cards.some(card => card.kind !== 'declarative') ||
      (project.spec.entrypoints && typeof project.spec.entrypoints === 'object' && Object.keys(project.spec.entrypoints).length)) {
    throw new Error('Add a Python starter only to a declarative project. Open the existing Python implementation for this project.');
  }
  const primaryPath = 'src/oaw_custom/__init__.py';
  const guidePath = 'PYTHON_SETUP.md';
  const occupied = [...project.files.map(file => file.path), ...pendingPaths];
  if (occupied.some(path => path === guidePath || path === 'src/oaw_custom' || path.startsWith('src/oaw_custom/'))) {
    throw new Error('The Python starter files already exist. Open them in Code; existing source will not be replaced.');
  }
  const literal = (value: string) => JSON.stringify(value);
  const owner = typeof project.spec.owner === 'string' ? project.spec.owner : project.distribution_id;
  const packs = Array.isArray(project.spec.packs) && project.spec.packs.every(value => typeof value === 'string')
    ? project.spec.packs as string[] : [`${owner}.default`];
  const cards = project.cards.filter(card => card.design);
  if (!cards.length) throw new Error('Select a visual card with an action contract before adding a Python starter.');
  const handlers = cards.map((card, index) => {
    const design = card.design!;
    const actions = [...new Set([design.action, ...(design.function.button_bindings ?? []).map(binding => binding.logic_id),
      ...(card.actions ?? []).map(action => action.id)])];
    return { card, actions, handler: `run_card_${index + 1}` };
  });
  const source = [
    '"""Python starter. This file is inactive until PYTHON_SETUP.md is completed.',
    'Visual resources stay in designs/; this module owns behavior after activation.',
    '"""',
    'from dataclasses import replace',
    '',
    'from open_agent_world.plugin_api import (',
    '    NodeResourceAction, PackDefinition, PluginDefinition, PluginDescriptor,',
    ')',
    '',
    ...handlers.flatMap(({ card, actions, handler }) => [
      '',
      `def ${handler}(context, arguments):`,
      `    # Card: ${literal(card.id)}; actions: ${actions.map(literal).join(', ')}`,
      `    # Read the current input definitions in ${literal(card.design_path ?? 'the card design resource')}.`,
      ...card.design!.function.fields.map(field => `    # ${field.key}: ${field.type}; default = ${JSON.stringify(field.default)}`),
      '    # Validate arguments, then return the result under the configured key:',
      `    # return {${literal(card.design!.result_key)}: your_result}`,
      `    raise NotImplementedError(${literal(`Implement ${card.id} before connecting this Python starter`)})`,
    ]),
    '', '',
    'def register(registration):',
    '    # The normal Schema 3 loader registers contributions before this hook.',
    ...handlers.flatMap(({ card, actions, handler }) => [
      `    card_id = ${literal(card.id)}`,
      '    card = registration.nodes[card_id]',
      '    registration.nodes[card_id] = replace(',
      '        card, resource_actions={',
      '            **card.resource_actions,',
      ...actions.map(action => `            ${literal(action)}: NodeResourceAction(${handler}),`),
      '        },',
      '    )',
    ]),
    ...packs.map(pack => `    registration.register_pack(PackDefinition(id=${literal(pack)}, name=${literal(project.name)}, cards=tuple(registration.nodes)))`),
    '', '',
    'def create_plugin():',
    '    return PluginDefinition(PluginDescriptor(',
    `        id=${literal(owner)}, version=${literal(project.version)},`,
    `        plugin_api_version="1.39", name=${literal(project.name)},`,
    '    ), register)',
    '',
  ].join('\n');
  const guide = `# Connect this Python starter\n\n` +
    `The Pack IDE added \`${primaryPath}\` without changing \`pyproject.toml\`, \`oaw.pack.toml\`, or any visual resource. ` +
    `Your current no-code behavior is still active. Saving this starter alone does not add Python to the Pack.\n\n` +
    `## 1. Implement the action contracts\n\n` +
    `Replace every \`NotImplementedError\` in the starter with validated behavior. Each handler receives an arguments dictionary and must return a dictionary containing its documented result key. ` +
    `Keep the card/action IDs aligned with these existing resources:\n\n` +
    handlers.map(({ card, actions }) => `- \`${card.id}\`: \`${card.design_path}\`; actions ${actions.map(action => `\`${action}\``).join(', ')}; result \`${card.design!.result_key}\`.`).join('\n') +
    `\n\nConnecting Python below makes Python responsible for these actions. The previous template/sum/multiply/join operation will no longer execute automatically. ` +
    `Implement any behavior you want to retain before connecting it. Visual appearance and bindings stay in their original JSON files; later visual edits never rewrite this Python. ` +
    `If inputs need persisted configuration defaults, add them to each contribution's \`config_schema\`; the automatic no-code input defaults are not injected for Python cards.\n\n` +
    `## 2. Connect the entrypoint when the behavior is ready\n\n` +
    `Merge these tables into \`pyproject.toml\` (do not duplicate existing tables). Keep its project name/version and existing dependencies; add any libraries your code imports to \`project.dependencies\`. ` +
    `The host supplies \`open_agent_world.plugin_api\`.\n\n` +
    '```toml\n[project.entry-points."open_agent_world.plugins"]\n' +
    `${literal(owner)} = "oaw_custom:create_plugin"\n\n` +
    '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n' +
    '[tool.hatch.build.targets.wheel]\npackages = ["src/oaw_custom"]\n```\n\n' +
    `Keep \`oaw.pack.toml\` contributions, \`[designs]\`, ownership and Pack IDs intact. Its \`[compatibility]\` must set \`plugin_api = "1.39"\`. ` +
    `When raising the project version, also update \`create_plugin()\` to the same version.\n\n` +
    `## 3. Build outside the IDE, then validate in a test profile\n\n` +
    `In an external terminal or authorized Sandbox, from this project directory and its chosen Python environment:\n\n` +
    '```sh\npython -m pip install build\npython -m build --wheel\n```\n\n' +
    `Use OAW's own Python interpreter for trusted plugin validation:\n\n` +
    '```sh\npython /path/to/OAW/scripts/oaw-pack.py doctor .\npython /path/to/OAW/scripts/oaw-pack.py check . --lifecycle\n```\n\n' +
    `The \`check --lifecycle\` command imports and runs trusted project code. Use a disposable development environment. ` +
    `The IDE's Check remains a static check and does not execute this Python.\n\n` +
    `Return to the IDE, refresh external changes, run Check, and Build / Export with the rebuilt wheel from \`dist/\`. ` +
    `Keep exactly one matching current wheel there for automatic selection, or select the wheel path in build options. ` +
    `Install the exported \`.oawpack\` using the existing installer in a test profile, restart when required, and invoke the real card action. ` +
    `A preview before installation does not establish that the Python works. Rebuild the wheel after every Python change.\n`;
  return { primaryPath, files: { [primaryPath]: source, [guidePath]: guide } };
}
