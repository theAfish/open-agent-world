// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { PluginCatalog, WorldCard } from '../types/world';
import { useStateMachineEditor } from './editorStore';
import { StateMachineEditor } from './StateMachineEditor';
import type { StateMachine } from './model';
import { StateMachineButton } from '../cards/CardUtilities';

vi.mock('./MachineGraph', () => ({ MachineGraph: ({ machine, onSelect }: {machine: StateMachine; onSelect: (selection: {entityId:string;stateId:string}) => void}) => <div data-testid="graph">{machine.entities.flatMap(entity => entity.states.map(state => <button key={`${entity.id}/${state.id}`} onClick={() => onSelect({entityId:entity.id,stateId:state.id})}>{entity.label}: {state.label}</button>))}</div> }));
const card = (id: string, name: string, type = 'legion') => ({ id, name, type, config: {} }) as WorldCard;
const root = card('root', 'Outer Legion'), inner = card('inner', 'Inner Legion'), worker = card('worker', 'Worker', 'agent');
const catalog = { node_types: [{id:'legion',container:{}, traits:[], state_machine_editor:true}, {id:'agent',traits:['core.agent'], state_machine_editor:true}, {id:'sandbox',traits:[],state_machine_editor:false}, {id:'folder',container:{},traits:[],state_machine_editor:false}], plugins: [], relationships: [], packs: [] } as unknown as PluginCatalog;
const document = (object: WorldCard) => ({ definition: {version:2 as const, entities:[{id:object.id,card_id:object.id,label:object.name,kind:'card' as const,initial_state:'planning',states:[{id:'planning',label:'Planning'}, {id:'review',label:'Awaiting review'}]}],rules:[]}, revision:1,definition_version:1,enabled:false,presentation:{} });
beforeEach(() => {
  useLocale.setState({ locale: 'en' });
  useWorldStore.setState({ cards: [root,inner,worker], edges:[], catalog });
  useStateMachineEditor.setState({activeId:'root'});
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable:true,value:function (this: HTMLDialogElement) { this.setAttribute('open',''); }});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable:true,value:function (this: HTMLDialogElement) { this.removeAttribute('open'); }});
  vi.spyOn(worldApi, 'getStateMachine').mockImplementation(async id => document([root,inner,worker].find(card => card.id === id)!));
  vi.spyOn(worldApi, 'getStateMachineEvents').mockResolvedValue({events:[],operations:[],sources:[]});
  vi.spyOn(worldApi, 'getStateMachineRuntime').mockResolvedValue({instances:[],actions:[],diagnostics:[]});
  vi.spyOn(worldApi, 'getStateMachineMembers').mockImplementation(async id => id === 'root' ? [{id:'inner',name:'Inner Legion',type:'legion',has_definition:true,state_machine_editor:true}, ...Array.from({length:500}, (_,n) => ({id:`unopened${n}`,name:`Other member ${n}`,type:'agent',has_definition:true,state_machine_editor:true}))] : id === 'inner' ? [{id:'worker',name:'Worker',type:'agent',has_definition:true,state_machine_editor:true}] : []);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

function systemDocument() {
  const doc = document(worker);
  return {...doc, definition: {...doc.definition, status_entity_id: 'execution', entities: [
    {id: 'execution', card_id: worker.id, label: 'Execution', kind: 'card' as const, ownership: 'system' as const, owner: 'host.run_manager', initial_state: 'idle',
      states: [{id: 'idle', label: 'Idle'}, {id: 'running', label: 'Running'}],
      projection: [{event: 'agent.work_started', from_state: '*', to_state: 'running'}],
      commands: [{id: 'start_work', label: 'Start work', kind: 'run' as const, operation_id: 'host:run', authorization: [], input_schema: {}, outcomes: ['running']}]},
    {...doc.definition.entities[0], label: 'Workflow'},
  ]}};
}

it('locks system state semantics and creates entered reactions from one referenced state', async () => {
  useStateMachineEditor.setState({activeId: worker.id});
  vi.mocked(worldApi.getStateMachine).mockResolvedValue(systemDocument());
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...systemDocument(), definition, presentation}));
  render(<StateMachineEditor />);
  fireEvent.click(await screen.findByRole('button', {name: 'Execution: Running'}));
  expect(screen.queryByLabelText('State name')).toBeNull();
  expect(screen.queryByRole('button', {name: 'Delete state'})).toBeNull();
  expect((screen.getByRole('button', {name: 'Add state'}) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole('button', {name: 'Close contextual editor'}));
  expect(screen.queryByRole('button', {name: 'State references'})).toBeNull();
  expect(screen.getByRole('button', {name: 'Execution: Idle'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'Connect states'}));
  fireEvent.click(screen.getByRole('button', {name: 'Execution: Running'}));
  fireEvent.click(screen.getByRole('button', {name: 'Workflow: Planning'}));
  expect((screen.getByLabelText('State phase') as HTMLSelectElement).value).toBe('state.entered');
  expect(screen.queryByLabelText('Trigger / interface')).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0][1].rules[0].trigger).toEqual({entity_id: 'execution', state_id: 'running', event: 'state.entered'});
  expect(save.mock.calls[0][1].entities).toHaveLength(2);
});

it('shows a legal operation and input for an edge toward a system state', async () => {
  useStateMachineEditor.setState({activeId: worker.id});
  vi.mocked(worldApi.getStateMachine).mockResolvedValue(systemDocument());
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...systemDocument(), definition, presentation}));
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name: 'Execution: Running'});
  fireEvent.click(screen.getByRole('button', {name: 'Connect states'}));
  fireEvent.click(screen.getByRole('button', {name: 'Workflow: Planning'}));
  fireEvent.click(screen.getByRole('button', {name: 'Execution: Running'}));
  expect((screen.getByLabelText('Via') as HTMLSelectElement).value).toBe('start_work');
  fireEvent.change(screen.getByLabelText('Input'), {target: {value: 'Research the material'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0][1].rules[0].effects).toEqual([]);
  expect(save.mock.calls[0][1].rules[0].command).toMatchObject({entity_id: 'execution', state_id: 'running', command_id: 'start_work', arguments: {prompt: 'Research the material'}});
});

it('starts with immutable anchors and lazily adds user states without replacing the system definition', async () => {
  useStateMachineEditor.setState({activeId: worker.id});
  const original = systemDocument();
  original.definition.entities = original.definition.entities.slice(0, 1);
  vi.mocked(worldApi.getStateMachine).mockResolvedValue(original);
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...original, definition, presentation}));
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name: 'Execution: Running'});
  expect(screen.getByRole('button', {name: 'State canvas'})).toBeTruthy();
  expect((screen.getByRole('button', {name: 'Save changes'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', {name: 'Execution: Running'}));
  fireEvent.click(screen.getByRole('button', {name: 'Add state'}));
  fireEvent.change(screen.getByLabelText('State name'), {target: {value: 'Researching'}});
  expect(screen.getByRole('button', {name: 'Execution: Running'})).toBeTruthy();
  expect(screen.getByRole('button', {name: 'User states: Researching'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'Close contextual editor'}));
  fireEvent.click(screen.getByRole('button', {name: 'State canvas'}));
  expect(screen.queryByRole('button', {name: 'Execution 🔒 SYSTEM'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: 'User states'}));
  expect(screen.getByRole('button', {name: 'User states: Researching'})).toBeTruthy();
  expect(screen.getByRole('button', {name: 'Execution: Running'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'Add state'}));
  fireEvent.change(screen.getByLabelText('State name'), {target: {value: 'Reviewing'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const definition = save.mock.calls[0][1];
  expect(definition.entities[0]).toEqual(original.definition.entities[0]);
  expect(definition.entities[1].ownership).toBe('user');
  expect(definition.entities[1].states.map(state => state.label)).toEqual(['Researching', 'Reviewing']);
  expect(definition.rules).toEqual([]);
});

it('shows the editor for supported types even without a definition and hides unsupported types with old definitions', () => {
  const {rerender} = render(<StateMachineButton card={worker} />);
  fireEvent.click(screen.getByRole('button', {name: 'State machine'}));
  expect(useStateMachineEditor.getState().activeId).toBe(worker.id);
  rerender(<StateMachineButton card={root} />);
  expect(screen.getByRole('button', {name: 'State machine'})).toBeTruthy();
  rerender(<StateMachineButton card={{...worker, type: 'sandbox', has_state_machine: true}} />);
  expect(screen.queryByRole('button', {name: 'State machine'})).toBeNull();
  rerender(<StateMachineButton card={{...worker, has_state_machine: true, ephemeral: true}} />);
  expect(screen.queryByRole('button', {name: 'State machine'})).toBeNull();
});

it('does not fabricate states, dirty a draft, or save when an undefined object is opened', async () => {
  vi.mocked(worldApi.getStateMachine).mockResolvedValue({definition: null, definition_version: 0, revision: 0, enabled: false, presentation: {}});
  const save = vi.spyOn(worldApi, 'saveStateMachine');
  render(<StateMachineEditor />);
  await screen.findByText('No states yet. Add a state to configure this workflow.');
  expect(screen.queryByTestId('graph')).toBeNull();
  expect((screen.getByRole('button', {name: 'Add state'}) as HTMLButtonElement).disabled).toBe(false);
  expect((screen.getByRole('button', {name: 'Save changes'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', {name: 'Close state machine editor'}));
  expect(useStateMachineEditor.getState().activeId).toBeUndefined();
  expect(save).not.toHaveBeenCalled();
});

it.each([root, worker])('creates and saves the first state of a new $type without enabling runtime', async object => {
  useStateMachineEditor.setState({activeId: object.id});
  vi.mocked(worldApi.getStateMachine).mockResolvedValue({definition: null, definition_version: 0, revision: 0, enabled: false, presentation: {}});
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...document(object), definition, presentation}));
  const enable = vi.spyOn(worldApi, 'enableStateMachine');
  render(<StateMachineEditor />);
  await screen.findByText('No states yet. Add a state to configure this workflow.');
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Add state'}));
  fireEvent.change(screen.getByLabelText('State name'), {target: {value: 'Researching'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const [id, definition, , revision] = save.mock.calls[0];
  expect(id).toBe(object.id); expect(revision).toBe(0);
  expect(definition.entities).toHaveLength(1);
  const group = definition.entities[0];
  expect(group.card_id).toBe(object.id);
  expect(group.states).toEqual([{id: group.initial_state, label: 'Researching'}]);
  expect(definition.rules).toEqual([]);
  expect(enable).not.toHaveBeenCalled();
});

it('opens a supported member before it has a saved definition', async () => {
  vi.mocked(worldApi.getStateMachineMembers).mockResolvedValue([{...worker, has_definition: false, state_machine_editor: true}]);
  vi.mocked(worldApi.getStateMachine).mockImplementation(async id => id === worker.id
    ? {definition: null, definition_version: 0, revision: 0, enabled: false, presentation: {}} : document(root));
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name: 'Outer Legion: Planning'});
  fireEvent.click(screen.getByRole('button', {name: 'Member'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Worker'}));
  await screen.findByText('No states yet. Add a state to configure this workflow.');
  fireEvent.click(screen.getByRole('button', {name: 'Add state'}));
  expect(screen.getByLabelText('State name')).toBeTruthy();
});

it('preserves developer-defined states and explicitly saves a type default without opening runtime', async () => {
  const own = document(root);
  own.definition.entities[0].initial_state = 'published';
  own.definition.entities[0].states = [{id: 'collecting', label: 'Collecting evidence'}, {id: 'published', label: 'Published'}];
  own.definition_version = 0; own.revision = 0;
  vi.mocked(worldApi.getStateMachine).mockResolvedValue(own);
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...own, definition, presentation, definition_version: 1, revision: 1}));
  const enable = vi.spyOn(worldApi, 'enableStateMachine');
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name: 'Outer Legion: Published'});
  expect(screen.queryByText('Unsaved changes')).toBeNull();
  expect(screen.getByText('Node type definition')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0][1].entities[0].initial_state).toBe('published');
  expect(enable).not.toHaveBeenCalled();
});

it('browses a stateless container to reach defined descendants and omits stateless leaves', async () => {
  useWorldStore.setState({cards: [root, {...inner, type: 'folder'}, worker]});
  vi.mocked(worldApi.getStateMachine).mockImplementation(async id => id === inner.id
    ? {definition: null, definition_version: 0, revision: 0, enabled: false, presentation: {}} : document(id === root.id ? root : worker));
  vi.mocked(worldApi.getStateMachineMembers).mockImplementation(async id => id === root.id
    ? [{...inner, type: 'folder', has_definition: false, has_members: true}, {id: 'plain', name: 'Plain text', type: 'text', has_definition: false}]
    : [{...worker, has_definition: true, state_machine_editor: true}]);
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name: 'Outer Legion: Planning'});
  fireEvent.click(screen.getByRole('button', {name: 'Member'}));
  expect(screen.queryByRole('button', {name: 'Plain text'})).toBeNull();
  expect((screen.getByRole('button', {name: 'Show states'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', {name: 'Inner Legion'}));
  await screen.findByText('This object does not offer a state machine editor.');
  expect((screen.getByRole('button', {name: 'Add state'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', {name: 'Member'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Worker'}));
  await screen.findByRole('button', {name: 'Worker: Planning'});
});

it('starts a new group empty and uses the first authored state as its initial state', async () => {
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...document(root), definition, presentation, revision: 2, definition_version: 2}));
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name: 'Outer Legion: Planning'});
  fireEvent.click(screen.getByRole('button', {name: 'State canvas'}));
  fireEvent.click(screen.getByRole('button', {name: 'Add group'}));
  expect(screen.getByRole('button', {name: 'Outer Legion: Planning'})).toBeTruthy();
  expect((screen.getByRole('button', {name: 'Save changes'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', {name: 'Add state'}));
  fireEvent.change(screen.getByLabelText('State name'), {target: {value: 'Measuring'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const group = save.mock.calls[0][1].entities[1];
  expect(group.states.map(state => state.label)).toEqual(['Measuring']);
  expect(group.initial_state).toBe(group.states[0].id);
});

it('navigates nested members without fetching or rendering the remaining 500 graphs', async () => {
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name:'Outer Legion: Planning'});
  expect(worldApi.getStateMachine).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('complementary', {name:'Transition editor'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name:'Member'}));
  fireEvent.change(screen.getByLabelText('Search objects'), {target:{value:'Inner'}});
  fireEvent.click(await screen.findByRole('button', {name:'Inner Legion'}));
  await screen.findByRole('button', {name:'Inner Legion: Planning'});
  expect(screen.queryByRole('button',{name:'Outer Legion: Planning'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name:'Member'}));
  fireEvent.click(await screen.findByRole('button', {name:'Worker'}));
  await screen.findByRole('button', {name:'Worker: Planning'});
  expect(worldApi.getStateMachine).toHaveBeenCalledTimes(3);
  expect(worldApi.getStateMachineMembers).toHaveBeenCalledWith('root');
  expect(worldApi.getStateMachineMembers).toHaveBeenCalledWith('inner');
  fireEvent.click(screen.getByRole('button', {name:'Outer Legion'}));
  await screen.findByRole('button', {name:'Outer Legion: Planning'});
  expect(worldApi.getStateMachine).toHaveBeenCalledTimes(3);
});

it('writes a member edit only to the host document with its revision and separate presentation', async () => {
  const save = vi.spyOn(worldApi, 'saveStateMachine').mockImplementation(async (id, definition, presentation) => ({...document(worker),definition,presentation,revision:2,definition_version:2}));
  render(<StateMachineEditor />);
  await screen.findByRole('button', {name:'Outer Legion: Planning'});
  fireEvent.click(screen.getByRole('button',{name:'Member'}));
  fireEvent.click(await screen.findByRole('button',{name:'Inner Legion'}));
  await screen.findByRole('button',{name:'Inner Legion: Planning'});
  fireEvent.click(screen.getByRole('button',{name:'Member'}));
  fireEvent.click(await screen.findByRole('button',{name:'Worker'}));
  fireEvent.click(await screen.findByRole('button',{name:'Worker: Planning'}));
  fireEvent.change(screen.getByLabelText('State name'),{target:{value:'Researching'}});
  fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const [id,definition,presentation,revision] = save.mock.calls[0];
  expect(id).toBe('worker'); expect(revision).toBe(1);
  expect(definition.entities).toHaveLength(1);
  expect(definition.entities[0].states[0]).toEqual({id:'planning',label:'Researching'});
  expect(presentation.positions?.worker.planning).toBeTruthy();
});

it('refreshes only the current owner runtime and stops polling when closed', async () => {
  vi.useFakeTimers();
  let editor: ReturnType<typeof render>;
  await act(async () => { editor = render(<StateMachineEditor />); });
  expect(worldApi.getStateMachineRuntime).toHaveBeenCalledWith('root');
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(worldApi.getStateMachineRuntime).toHaveBeenCalledTimes(2);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Member' })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Inner Legion' })); });
  expect(worldApi.getStateMachineRuntime).toHaveBeenLastCalledWith('inner');
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(vi.mocked(worldApi.getStateMachineRuntime).mock.calls.map(([id]) => id)).toEqual(['root', 'root', 'inner', 'inner']);
  expect(worldApi.getStateMachine).toHaveBeenCalledTimes(2);
  editor!.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(worldApi.getStateMachineRuntime).toHaveBeenCalledTimes(4);
});
