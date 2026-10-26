import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Braces, CirclePlay, MousePointer2, Plus, RotateCcw, Trash2, Type } from 'lucide-react';
import { apiErrorMessage } from '../api/client';
import type { NodeSurfaceLevel } from '../types/world';
import { FaceArtwork, elementStyle } from './FaceArtwork';
import { FaceFieldInput, FaceFunctionElement } from './FaceFunctionElements';
import { faceStudio, presetSurface } from './faceDesign';
import { buttonAction, buttonLabel } from './faceButtons';
import { recipeProduction, recipeSettings } from './designRecipes';
import { newPrintLayer } from '../cards/cardProduction';
import type { CardInterface } from './projectTypes';
import type { FaceDesign, FaceElement, FunctionDesign, InputField, Scalar, SurfaceDesign } from './types';
import './packVisualEditor.css';

export interface PackVisualEditorProps {
  design: CardInterface;
  onChange: (design: CardInterface) => void;
  mode: 'design' | 'function';
  level: NodeSurfaceLevel;
  onLevelChange: (level: NodeSurfaceLevel) => void;
  actions?: { id: string; label?: string }[];
  cardKind?: 'declarative' | 'python';
  run?: (action: string, values: Record<string, Scalar>) => Promise<unknown>;
  runDisabledReason?: string;
  runContext?: string;
  undo?: () => void;
  canUndo?: boolean;
}

const SURFACES: Record<NodeSurfaceLevel, string> = { node: 'Node', preview: 'Card', inspector: 'Detail', workspace: 'Workspace' };
const ELEMENT_NAMES: Record<FaceElement['kind'], string> = {
  title: 'Title', subtitle: 'Subtitle', description: 'Description', icon: 'Icon', metadata: 'Metadata', status: 'Status',
  tags: 'Tags', illustration: 'Image', badge: 'Badge', help: 'Help', fields: 'Inputs', action: 'Run', button: 'Button', result: 'Result', text: 'Text',
};
type FunctionSection = 'Inputs' | 'Actions' | 'State' | 'Results';
const elementName = (element: FaceElement, face: FaceDesign) => buttonAction(element) ? buttonLabel(element, face) : ELEMENT_NAMES[element.kind];
const outputText = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);

/** Edits the same portable artwork and action addresses that the installed card consumes. */
export function PackVisualEditor({ design, onChange, mode, level, onLevelChange, actions = [], cardKind = 'declarative', run, runDisabledReason, runContext, undo, canUndo }: PackVisualEditorProps) {
  const studio = faceStudio(design.face);
  const surface = studio.modes[level] ?? presetSurface(level, design.face);
  const surfaceSlot = level === 'inspector' ? 'body' : level === 'workspace' ? 'workspace' : 'preview';
  const [selected, setSelected] = useState('title');
  const [fieldIndex, setFieldIndex] = useState(0);
  const [section, setSection] = useState<FunctionSection>('Actions');
  const [values, setValues] = useState<Record<string, Scalar>>({});
  const [result, setResult] = useState<unknown>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [lastAction, setLastAction] = useState('');
  const [scale, setScale] = useState(0.8);
  const stage = useRef<HTMLDivElement>(null);
  const active = useRef(false);
  const element = surface.elements.find(item => item.id === selected);
  const field = design.function.fields[fieldIndex];
  const selectedButtonAction = element && buttonAction(element);
  const selectedAction = selectedButtonAction === 'custom'
    ? design.function.button_bindings?.find(binding => binding.mode === level && binding.element_id === element?.id)?.logic_id ?? ''
    : !selectedButtonAction || selectedButtonAction === 'run' ? design.action : '';
  const knownActions = actions.some(action => action.id === design.action) || !design.action
    ? actions : [{ id: design.action, label: cardKind === 'declarative' ? 'Card function' : design.action }, ...actions];

  useEffect(() => {
    const container = stage.current;
    if (!container) return;
    const fit = () => setScale(Math.max(0.2, Math.min(1, (container.clientWidth - 72) / surface.width, (container.clientHeight - 80) / surface.height)));
    fit();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(fit);
    observer.observe(container);
    return () => observer.disconnect();
  }, [surface.width, surface.height]);

  const changeFace = (patch: Partial<FaceDesign>) => onChange({ ...design, face: { ...design.face, ...patch, studio: patch.studio ?? studio } });
  const changeFunction = (patch: Partial<FunctionDesign>) => onChange({ ...design, function: { ...design.function, ...patch } });
  const changeSurface = (next: SurfaceDesign) => changeFace({ studio: { ...studio, modes: { ...studio.modes, [level]: next } } });
  const changeElement = (patch: Partial<FaceElement>) => {
    if (!element) return;
    changeSurface({ ...surface, elements: surface.elements.map(item => item.id === element.id ? { ...item, ...patch } : item) });
  };
  const selectElement = (item: FaceElement, index?: number) => {
    setSelected(item.id);
    if (index !== undefined) setFieldIndex(index);
    if (item.kind === 'fields') setSection('Inputs');
    if (item.kind === 'button' || item.kind === 'action') setSection('Actions');
    if (item.kind === 'result') setSection('Results');
    if (item.kind === 'status') setSection('State');
  };
  const updateField = (patch: Partial<InputField>) => changeFunction({ fields: design.function.fields.map((item, index) => index === fieldIndex ? { ...item, ...patch } : item) });
  const newElement = (kind: FaceElement['kind']): FaceElement => {
    let index = 1;
    while (surface.elements.some(item => item.id === `${kind}-${index}`)) index++;
    const width = Math.max(64, Math.min(kind === 'button' || kind === 'text' ? 190 : surface.width, surface.width - 48));
    const height = kind === 'fields' ? 90 : kind === 'result' ? 70 : 40;
    const occupied = surface.elements.filter(item => item.kind !== 'help' || design.face.help_text);
    let position = { x: 24, y: Math.min(2048 - height - 24, Math.max(24, ...occupied.map(item => item.y + item.height + 12))) };
    findSpace: for (let y = 24; y + height <= surface.height - 24; y += 8) {
      for (const x of [24, surface.width - width - 24]) {
        if (occupied.every(item => x + width + 8 <= item.x || x >= item.x + item.width + 8 || y + height + 8 <= item.y || y >= item.y + item.height + 8)) {
          position = { x, y }; break findSpace;
        }
      }
    }
    return { id: `${kind}-${index}`, kind, ...position, width, height,
      text: kind === 'button' ? 'Run action' : kind === 'text' ? 'Your text' : '', font_size: 14, color: kind === 'button' ? '#ffffff' : surface.elements.find(item => item.kind === 'title')?.color ?? '#283c32', align: 'left',
      placement: 'free', sizing: 'fixed', ...(kind === 'button' ? { button: { action: 'custom' as const, background: design.face.color, radius: 8 } } : {}) };
  };
  const appendElement = (item: FaceElement) => {
    const height = Math.max(surface.height, item.y + item.height + 24);
    const next = { ...surface, height, elements: [...surface.elements, item], shapes: surface.shapes.map((shape, index) =>
      index === 0 && shape.height === surface.height && shape.y === 0 ? { ...shape, height } : shape) };
    const settings = recipeSettings(design.face, surface), production = recipeProduction(settings);
    if (!production.print.layered || production.layers?.some(layer => layer.kind === 'ink' && layer.enabled && layer.content?.source === 'all')) return next;
    const layers = production.layers ?? [], ink = layers.find(layer => layer.kind === 'ink' && layer.enabled && layer.strength > 0);
    const inkLayers = ink ? layers.map(layer => layer.id === ink.id ? { ...layer, content: { source: 'elements' as const, elementIds: [...layer.content?.elementIds ?? [], item.id] } } : layer)
      : [...layers, { ...newPrintLayer(), content: { source: 'elements' as const, elementIds: [item.id] } }];
    return { ...next, design: { ...settings, production: { ...production, layers: inkLayers } } };
  };
  const addElement = (kind: FaceElement['kind']) => {
    const item = newElement(kind);
    const next = { ...design, face: { ...design.face, studio: { ...studio, modes: { ...studio.modes, [level]: appendElement(item) } } } };
    if (kind === 'button' && design.action) next.function = { ...design.function, button_bindings: [...design.function.button_bindings ?? [], { mode: level, element_id: item.id, logic_id: design.action }] };
    onChange(next);
    selectElement(item);
  };
  const addField = () => {
    let index = design.function.fields.length + 1;
    while (design.function.fields.some(item => item.key === `field${index}`)) index++;
    const fields = [...design.function.fields, { key: `field${index}`, label: `Field ${index}`, type: 'text' as const, default: '', required: false }];
    const container = surface.elements.find(item => item.kind === 'fields') ?? newElement('fields');
    onChange({ ...design, function: { ...design.function, fields }, face: { ...design.face, studio: { ...studio, modes: { ...studio.modes,
      [level]: surface.elements.some(item => item.id === container.id) ? surface : appendElement(container) } } } });
    selectElement(container, fields.length - 1);
  };
  const removeElement = () => {
    if (!element) return;
    onChange({ ...design, face: { ...design.face, studio: { ...studio, modes: { ...studio.modes, [level]: { ...surface, elements: surface.elements.filter(item => item.id !== element.id) } } } },
      function: { ...design.function, button_bindings: design.function.button_bindings?.filter(binding => binding.mode !== level || binding.element_id !== element.id) } });
    setSelected('');
  };
  const bindAction = (logic_id: string) => {
    if (!element || buttonAction(element) !== 'custom') { onChange({ ...design, action: logic_id }); return; }
    changeFunction({ button_bindings: [...(design.function.button_bindings ?? []).filter(binding => binding.mode !== level || binding.element_id !== element.id),
      ...(logic_id ? [{ mode: level, element_id: element.id, logic_id }] : [])] });
  };
  const test = async () => {
    if (!run || !selectedAction || active.current) return;
    active.current = true; setBusy(true); setError(''); setResult(undefined); setLastAction(selectedAction);
    try { setResult(await run(selectedAction, Object.fromEntries(design.function.fields.map(item => [item.key, values[item.key] ?? item.default])))); }
    catch (cause) { setError(apiErrorMessage(cause)); }
    finally { active.current = false; setBusy(false); }
  };
  const textValue = element?.kind === 'title' ? design.face.title : element?.kind === 'description' ? design.face.description
    : element?.kind === 'help' ? design.face.help_text : element?.kind === 'action' ? design.face.button_label : element?.text ?? '';
  const setText = (value: string) => {
    if (!element) return;
    if (element.kind === 'title') changeFace({ title: value });
    else if (element.kind === 'description') changeFace({ description: value });
    else if (element.kind === 'help') changeFace({ help_text: value });
    else if (element.kind === 'action') changeFace({ button_label: value });
    else changeElement({ text: value });
  };
  const fieldEditor = <>
    <div className="pack-visual-items" aria-label="Input fields">{design.function.fields.map((item, index) => <button key={index} type="button" aria-pressed={index === fieldIndex}
      onClick={() => setFieldIndex(index)}><span>{item.label || item.key}</span><code>{item.type}</code></button>)}</div>
    {field ? <div className="pack-visual-properties">
      <label>Field label<input value={field.label} maxLength={80} onChange={event => updateField({ label: event.target.value })} /></label>
      <div className="pack-visual-pair"><label>Field key<input value={field.key} maxLength={40} onChange={event => updateField({ key: event.target.value })} /></label>
        <label>Field type<select value={field.type} onChange={event => updateField({ type: event.target.value as InputField['type'], default: event.target.value === 'number' ? 0 : event.target.value === 'boolean' ? false : '' })}>
          <option value="text">Text</option><option value="number">Number</option><option value="boolean">Boolean</option></select></label></div>
      <FaceFieldInput field={{ ...field, label: 'Default value', required: false }} value={field.default} onChange={value => updateField({ default: value })} />
      <div className="pack-visual-between"><label className="factory-check"><input type="checkbox" checked={field.required} onChange={event => updateField({ required: event.target.checked })} />Required</label>
        <button type="button" className="pack-visual-icon" aria-label="Remove selected field" onClick={() => { changeFunction({ fields: design.function.fields.filter((_, index) => index !== fieldIndex) }); setFieldIndex(Math.max(0, fieldIndex - 1)); }}><Trash2 size={14} /></button></div>
    </div> : <p className="pack-visual-hint">Add an input to collect a value for your action.</p>}
  </>;
  const bindingEditor = <div className="pack-visual-properties">
    {element?.kind === 'button' && <label>Button behavior<select value={buttonAction(element)} onChange={event => changeElement({ button: { ...element.button, action: event.target.value as NonNullable<FaceElement['button']>['action'] } })}>
      <option value="custom">Call an action</option><option value="run">Run main action</option><option value="open">Open card</option><option value="collapse">Collapse card</option><option value="surface">Switch surface</option><option value="delete">Delete card</option></select></label>}
    {(!element || !buttonAction(element) || ['run', 'custom'].includes(buttonAction(element)!)) && <label>Bound action<select value={selectedAction} onChange={event => bindAction(event.target.value)}>
      <option value="">Choose action…</option>{knownActions.map(action => <option key={action.id} value={action.id}>{action.label && action.label !== action.id ? `${action.label} · ${action.id}` : action.id}</option>)}</select></label>}
    {element && buttonAction(element) && !['run', 'custom'].includes(buttonAction(element)!) && <p className="pack-visual-hint">This button uses the built-in card {buttonAction(element)} action.</p>}
  </div>;

  return <div className="pack-visual-editor" data-mode={mode}>
    <div className="pack-visual-workspace">
      <div className="pack-visual-toolbar">
        <label className="pack-visual-surface">Surface<select aria-label="Surface" value={level} onChange={event => onLevelChange(event.target.value as NodeSurfaceLevel)}>
          {Object.entries(SURFACES).filter(([value]) => studio.enabled.includes(value as NodeSurfaceLevel)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <span className="pack-visual-divider" />
        <button type="button" title="Add input field" aria-label="Add field" disabled={design.function.fields.length >= 20 || (surface.elements.length >= 32 && !surface.elements.some(item => item.kind === 'fields'))} onClick={addField}><Plus size={14} /><span>Field</span></button>
        <button type="button" title="Add action button" aria-label="Add button" disabled={surface.elements.length >= 32} onClick={() => addElement('button')}><MousePointer2 size={14} /><span>Button</span></button>
        <button type="button" title="Add text" aria-label="Add text" disabled={surface.elements.length >= 32} onClick={() => addElement('text')}><Type size={14} /><span>Text</span></button>
        <button type="button" title="Add result display" aria-label="Add result" disabled={surface.elements.length >= 32} onClick={() => addElement('result')}><ArrowDownToLine size={14} /><span>Result</span></button>
        {undo && <button type="button" className="pack-visual-undo" title="Undo visual edit" aria-label="Undo visual edit" disabled={!canUndo} onClick={undo}><RotateCcw size={14} /></button>}
      </div>
      <div className="pack-visual-stage" ref={stage} role="region" aria-label="Card canvas">
        <span className="pack-visual-canvas-caption">{SURFACES[level]} · {surface.width} × {surface.height}{!design.surfaces.includes(surfaceSlot) && ' · Visual draft, not applied'}</span>
        <div className="pack-visual-scaled" style={{ width: surface.width * scale, height: surface.height * scale }}>
          <div className="pack-visual-card factory-designed-card" style={{ width: surface.width, height: surface.height, transform: `scale(${scale})` }}>
            <FaceArtwork face={design.face} surface={surface} functionDesign={design.function} level={level} interactive={false} render={item => {
              if (item.kind === 'fields') return <div className="factory-fields factory-face-fields pack-visual-fields" data-layout={surface.field_layout}>
                {design.function.fields.map((input, index) => {
                  const value = mode === 'function' ? values[input.key] ?? input.default : input.default;
                  return <button type="button" key={index} aria-label={`Select ${input.label}`} aria-pressed={selected === item.id && fieldIndex === index}
                    onClick={() => selectElement(item, index)}><span>{input.label}{input.required ? ' *' : ''}</span><span className="pack-visual-field-value">{input.type === 'boolean' ? value ? '☑' : '☐' : String(value) || 'Enter value…'}</span></button>;
                })}
                {!design.function.fields.length && <button type="button" className="pack-visual-empty-input" onClick={addField}>+ Add an input</button>}
              </div>;
              if (item.kind === 'result') return <output className="factory-result factory-face-result">{result === undefined ? 'Action result appears here' : outputText(result)}</output>;
              if (item.kind === 'status') return <span>{busy ? 'Running' : error ? 'Failed' : result !== undefined ? 'Complete' : item.text || 'Idle'}</span>;
              if (item.kind === 'button' || item.kind === 'action') return <FaceFunctionElement element={item} face={design.face} surface={surface} level={level} thumbnail />;
              return undefined;
            }}>
              <div className="pack-visual-hit-layer">{surface.elements.filter(item => item.kind !== 'fields').map(item => <button type="button" key={item.id}
                className="pack-visual-hit" aria-label={`Select ${elementName(item, design.face)}`} aria-pressed={selected === item.id}
                style={elementStyle(item, surface)} onClick={() => selectElement(item)} onKeyDown={event => {
                  const delta = event.shiftKey ? 10 : 1;
                  const movement = ({ ArrowLeft: { x: item.x - delta }, ArrowRight: { x: item.x + delta }, ArrowUp: { y: item.y - delta }, ArrowDown: { y: item.y + delta } })[event.key];
                  if (movement) { event.preventDefault(); changeSurface({ ...surface, elements: surface.elements.map(value => value.id === item.id ? { ...value, ...movement, placement: 'free' } : value) }); }
                }}><span className="pack-visual-selection-label">{elementName(item, design.face)}</span></button>)}</div>
            </FaceArtwork>
          </div>
        </div>
        <span className="pack-visual-canvas-hint"><MousePointer2 size={12} />Click an element to edit · Arrow keys move selection</span>
      </div>
      {mode === 'function' && <div className="pack-visual-runbar">
        {runContext && <p className="pack-visual-run-context">{runContext}</p>}
        <div><CirclePlay size={16} /><span>{busy ? 'Running action…' : error ? 'Action failed' : result !== undefined ? 'Action complete' : 'Test with input values'}<small>{lastAction || selectedAction || 'Choose an action'}</small></span></div>
        <button type="button" disabled={!run || busy || !selectedAction} title={!run ? runDisabledReason : undefined} onClick={() => void test()}>{busy ? 'Running…' : 'Test action'}</button>
        {error && <p role="alert">{error}</p>}
        {result !== undefined && <output aria-label="Action test result">{outputText(result)}</output>}
        {!run && runDisabledReason && <p>{runDisabledReason}</p>}
        {run && selectedButtonAction && !['run', 'custom'].includes(selectedButtonAction) && <p>Use Preview to try this built-in card action.</p>}
      </div>}
    </div>

    <aside className="pack-visual-inspector" aria-label="Element properties">
      <header><span>{mode === 'design' ? 'DESIGN' : 'FUNCTION'}</span><strong>{element ? elementName(element, design.face) : 'Card'}</strong><small>{element?.id ?? 'Select an element on the card'}</small></header>
      {mode === 'design' ? <>
        {element?.kind === 'fields' ? <section>{fieldEditor}<label>Input layout<select value={surface.field_layout} onChange={event => changeSurface({ ...surface, field_layout: event.target.value as SurfaceDesign['field_layout'] })}><option value="stack">Stack</option><option value="columns">Columns</option></select></label></section>
          : element && <section className="pack-visual-properties">
            {!['result', 'icon', 'illustration'].includes(element.kind) && <label>Text<textarea aria-label="Text" rows={element.kind === 'description' ? 3 : 2} value={textValue} onChange={event => setText(event.target.value)} /></label>}
            {element.kind === 'icon' && <label>Icon name<input value={design.face.icon} onChange={event => changeFace({ icon: event.target.value })} /></label>}
            <div className="pack-visual-pair"><label>Font size<input type="number" min={6} max={200} value={element.font_size} onChange={event => changeElement({ font_size: Number(event.target.value) || 14 })} /></label>
              <label>Text color<input type="color" value={element.color} onChange={event => changeElement({ color: event.target.value })} /></label></div>
            <label>Alignment<select value={element.align} onChange={event => changeElement({ align: event.target.value as FaceElement['align'] })}><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label>
            {(element.kind === 'button' || element.kind === 'action') && <label>Button color<input type="color" value={element.button?.background ?? design.face.color}
              onChange={event => element.kind === 'action' ? changeFace({ color: event.target.value }) : changeElement({ button: { action: buttonAction(element)!, ...element.button, background: event.target.value } })} /></label>}
          </section>}
        {element && <section className="pack-visual-properties"><h4>Position & size</h4><div className="pack-visual-geometry">{(['x', 'y', 'width', 'height'] as const).map(key => <label key={key}>{key === 'x' || key === 'y' ? key.toUpperCase() : key[0].toUpperCase() + key.slice(1)}<input type="number" value={element[key]} min={key === 'width' || key === 'height' ? 8 : -2048} max={2048}
          onChange={event => changeElement({ [key]: Number(event.target.value), placement: 'free' })} /></label>)}</div>
          <button type="button" className="pack-visual-delete" onClick={removeElement}><Trash2 size={13} />Remove element</button></section>}
        <section className="pack-visual-properties"><h4>Card appearance</h4><label className="factory-check"><input type="checkbox" checked={design.surfaces.includes(surfaceSlot)}
          onChange={event => onChange({ ...design, surfaces: event.target.checked ? [...design.surfaces, surfaceSlot] : design.surfaces.filter(slot => slot !== surfaceSlot) })} />Use visual design on this surface</label>
          {!design.surfaces.includes(surfaceSlot) && <small>The saved card keeps its existing {surfaceSlot} component until this is enabled.</small>}
          <div className="pack-visual-pair"><label>Accent<input type="color" value={design.face.color} onChange={event => changeFace({ color: event.target.value })} /></label>
          <label>Background<input type="color" value={surface.shapes[0]?.fill ?? '#ffffff'} onChange={event => changeSurface({ ...surface, shapes: surface.shapes.map((shape, index) => index === 0 ? { ...shape, fill: event.target.value } : shape) })} /></label></div></section>
      </> : <>
        <nav className="pack-visual-function-tabs" aria-label="Function model">{(['Inputs', 'Actions', 'State', 'Results'] as const).map(item => <button key={item} type="button" aria-pressed={section === item} onClick={() => setSection(item)}>{item}</button>)}</nav>
        <section className="pack-visual-properties">
          {section === 'Inputs' && <>{fieldEditor}<details><summary>Test input values</summary><div className="pack-visual-properties">{design.function.fields.map(item => <FaceFieldInput key={item.key} field={item} value={values[item.key] ?? item.default} onChange={value => setValues(current => ({ ...current, [item.key]: value }))} />)}</div></details></>}
          {section === 'Actions' && <>
            {bindingEditor}
            <div className="pack-visual-action-identity"><Braces size={14} /><code>{selectedAction || 'No action bound'}</code><small>{cardKind === 'python' ? 'Python resource action' : 'Declarative card function'}</small></div>
            {cardKind === 'declarative' ? <>
              <label>Operation<select value={design.function.operation} onChange={event => changeFunction({ operation: event.target.value as FunctionDesign['operation'] })}>
                <option value="template">Text template</option><option value="sum">Sum numbers</option><option value="multiply">Multiply numbers</option><option value="join">Join values</option></select></label>
              {design.function.operation === 'template' && <label>Output template<textarea aria-label="Output template" rows={4} value={design.function.template} maxLength={10000} onChange={event => changeFunction({ template: event.target.value })} /><small>Insert an input with {'{{field_key}}'}.</small></label>}
              {design.function.operation === 'join' && <label>Separator<input value={design.function.separator} maxLength={100} onChange={event => changeFunction({ separator: event.target.value })} /></label>}
            </> : <p className="pack-visual-hint">Implementation lives in the Python source. These bindings pass the Inputs as action arguments.</p>}
            <div className="pack-visual-contract"><span>Inputs</span><code>{design.function.fields.map(item => `${item.key}: ${item.type}`).join('\n') || 'No arguments'}</code><span>Result</span><code>{design.result_key || 'Whole response'}</code></div>
            <details><summary>Test input values</summary><div className="pack-visual-properties">{design.function.fields.map(item => <FaceFieldInput key={item.key} field={item} value={values[item.key] ?? item.default} onChange={value => setValues(current => ({ ...current, [item.key]: value }))} />)}</div></details>
          </>}
          {section === 'State' && <><h4>Action test state</h4><dl className="pack-visual-state"><dt>Status</dt><dd>{busy ? 'Running' : error ? 'Failed' : result !== undefined ? 'Complete' : 'Idle'}</dd><dt>Last action</dt><dd><code>{lastAction || 'Not run'}</code></dd></dl>
            <p className="pack-visual-hint">This is the current test session. Persistent state and lifecycle behavior remain in the card implementation.</p></>}
          {section === 'Results' && <><label>Result key<input value={design.result_key} maxLength={80} onChange={event => onChange({ ...design, result_key: event.target.value })} /><small>Response property shown in result elements.</small></label>
            <h4>Last test output</h4><pre className="pack-visual-output">{error || (result === undefined ? 'Run an action to see its output.' : outputText(result))}</pre></>}
        </section>
      </>}
    </aside>
  </div>;
}
