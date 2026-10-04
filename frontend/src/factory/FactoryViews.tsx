import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Download, Package, Plus, Printer as PrinterIcon, RefreshCw, Save, Share2, Trash2 } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { finishLabel } from '../cards/cardFinish';
import { FaceDesignerCanvas } from './FaceDesignerCanvas';
import { faceStudio } from './faceDesign';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { PackSurface } from '../shell/PackSurface';
import { PACK_DESIGNS, PACK_PACKAGING } from '../shell/packDesign';
import { FacePreview, FieldInput, FunctionForm } from './PrintedCardView';
import type { PluginViewProps } from '../plugins/sdk';
import type { LegionSummary, WorldCard } from '../types/world';
import type { BasketItem, FaceDesign, FactoryContext, FactoryInspection, FunctionDesign, InputField, PackDesign, PackerConfig, Scalar } from './types';
import './factory.css';

function useTask() {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const active = useRef(false);
  const perform = async (work: () => Promise<void>) => {
    if (active.current) return;
    active.current = true; setBusy(true); setError(''); setMessage('');
    try { await work(); } catch (cause) { setError(apiErrorMessage(cause)); }
    finally { active.current = false; setBusy(false); }
  };
  return { busy, error, message, setMessage, perform };
}

function useEditor<T extends object>({ card, host }: PluginViewProps) {
  const [draft, setDraft] = useState<T>(() => (host.draft?.get() ?? card.config) as T);
  useEffect(() => {
    const sync = () => setDraft((host.draft?.get() ?? card.config) as T);
    sync();
    return host.draft?.subscribe?.(sync);
  }, [host.draft, card.config]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(card.config);
  const change = (patch: Partial<T>) => {
    const next = { ...draft, ...patch }; setDraft(next); host.draft?.set(next as Record<string, unknown>);
  };
  const save = async (next: T = draft) => { await host.updateConfig(next as Record<string, unknown>); host.draft?.set(undefined); };
  return { draft, change, save, dirty };
}

function Frame({ title, step, children, task, level }: {
  title: string; step: string; children: ReactNode; task: ReturnType<typeof useTask>; level: string;
}) {
  return <section className="factory nodrag nowheel" data-level={level}>
    <header className="factory-heading"><span>{step}</span><strong>{title}</strong><small>卡包工厂 / 01</small></header>
    <fieldset disabled={task.busy}>{children}</fieldset>
    {task.error && <p className="factory-error" role="alert">{task.error}</p>}
    {task.message && <p className="factory-message" role="status">{task.message}</p>}
  </section>;
}

function SaveButton({ editor, task }: { editor: { save: () => Promise<void>; dirty: boolean }; task: ReturnType<typeof useTask> }) {
  return <div className="factory-actions"><button type="button" onClick={() => void task.perform(async () => { await editor.save(); task.setMessage('设计已保存，下次印刷使用此版本。'); })}>
    <Save size={14} />保存设计</button><small>{editor.dirty ? '有未保存更改' : '已保存'}</small></div>;
}

function ToolPreview({ id, children }: { id: string; children: ReactNode }) {
  return <div className="factory-preview nodrag nowheel">{children}<button type="button"
    onClick={() => useNodeSurfaceStore.getState().openWorkspace(id)}>打开编辑器</button></div>;
}

export function PackDesigner(props: PluginViewProps) {
  const editor = useEditor<PackDesign>(props), task = useTask(), { draft, change } = editor;
  const [opened, setOpened] = useState(false);
  const metadata = (key: keyof PackDesign['creator'], value: string) => change({ creator: { ...draft.creator, [key]: value } });
  if (props.level === 'preview') return <ToolPreview id={props.card.id}><strong>{draft.name}</strong><small>{draft.version} · {PACK_DESIGNS[draft.creator.packaging ?? 'standard'].label}</small><p>包装、发布信息与使用说明</p></ToolPreview>;
  return <Frame title="卡包设计器" step="01 / 包装" task={task} level={props.level}>
    <div className="factory-design-grid"><div className="factory-controls">
      <label>卡包名称<input maxLength={120} value={draft.name} onChange={event => change({ name: event.target.value })} /></label>
      <div className="factory-row"><label>Pack ID<input maxLength={100} value={draft.id} onChange={event => change({ id: event.target.value })} /></label>
        <label>版本<input maxLength={64} value={draft.version} onChange={event => change({ version: event.target.value })} /></label></div>
      <small>更新时保留 Pack ID，并递增版本号。</small>
      <div className="factory-row"><label>包装<select value={draft.creator.packaging} onChange={event => metadata('packaging', event.target.value)}>
        {PACK_PACKAGING.map(value => <option key={value} value={value}>{PACK_DESIGNS[value].label}</option>)}</select></label>
        <label>主题色<input type="color" value={draft.creator.accent_color} onChange={event => metadata('accent_color', event.target.value)} /></label></div>
      <label>作者<input maxLength={120} value={draft.creator.author} onChange={event => metadata('author', event.target.value)} /></label>
      <label>简介<textarea rows={3} maxLength={500} value={draft.creator.description} onChange={event => metadata('description', event.target.value)} /></label>
      <details><summary>使用说明</summary>{([['preparation', '准备步骤'], ['example', '示例任务'], ['expected_result', '预期结果']] as const).map(([key, label]) =>
        <label key={key}>{label}<textarea rows={2} maxLength={2000} value={draft.creator[key]} onChange={event => metadata(key, event.target.value)} /></label>)}</details>
    </div><div className="factory-pack-preview"><PackSurface definition={{ id: props.card.id, plugin_id: 'oaw.factory', name: draft.name,
      description: draft.creator.description, cards: [], packaging: draft.creator.packaging, accent_color: draft.creator.accent_color }}
      edition={draft.creator.author || '卡包工厂'} cards={[]} count={null} opened={opened} label="预览包装" sealLabel="预览包装" onClick={() => setOpened(!opened)} />
      <small>点击包装预览开合</small></div></div>
    <SaveButton editor={editor} task={task} /><p className="factory-hint">保存后，将此卡连接到打包器。</p>
  </Frame>;
}

export function FaceDesigner(props: PluginViewProps) {
  const editor = useEditor<FaceDesign>(props), task = useTask(), { draft, change } = editor;
  if (props.level === 'preview') return <ToolPreview id={props.card.id}><strong>{draft.title}</strong><small>{draft.variant} · {draft.tone} · {finishLabel(draft.finish)}</small><p>{draft.layout === 'columns' ? '双列' : '单列'}功能区 · {draft.button_label}</p></ToolPreview>;
  return <section className="factory factory-face-editor nodrag nowheel" data-level={props.level}>
    <fieldset disabled={task.busy}><FaceDesignerCanvas face={draft} onChange={change}
      saveControl={<SaveButton editor={{ ...editor, save: () => editor.save({ ...draft, studio: faceStudio(draft) }) }} task={task} />} /></fieldset>
    {task.error && <p className="factory-error" role="alert">{task.error}</p>}
    {task.message && <p className="factory-message" role="status">{task.message}</p>}
  </section>;
}

export function FunctionDesigner(props: PluginViewProps) {
  const editor = useEditor<FunctionDesign>(props), task = useTask(), { draft, change } = editor;
  const updateField = (index: number, patch: Partial<InputField>) => change({ fields: draft.fields.map((field, i) => i === index ? { ...field, ...patch } : field) });
  if (props.level === 'preview') return <ToolPreview id={props.card.id}><strong>{draft.fields.length} 个输入字段</strong><small>{draft.fields.map(field => field.label).join(' / ')}</small><p>{({ template: '文本模板', sum: '数值求和', multiply: '数值乘积', join: '内容拼接' })[draft.operation]}</p></ToolPreview>;
  return <Frame title="功能设计器" step="03 / 功能" task={task} level={props.level}>
    <p className="factory-hint">添加表单字段，再选择运行方式。模板使用 {'{{字段标识}}'} 插入内容。</p>
    <div className="factory-function-editor"><div className="factory-controls">
      {draft.fields.map((field, index) => <div className="factory-field-editor" key={index}>
        <div className="factory-row"><label>字段标识<input maxLength={40} value={field.key} onChange={event => updateField(index, { key: event.target.value })} /></label>
          <label>显示名称<input maxLength={80} value={field.label} onChange={event => updateField(index, { label: event.target.value })} /></label>
          <button type="button" className="factory-icon-button" aria-label={`删除字段 ${index + 1}`} onClick={() => change({ fields: draft.fields.filter((_, i) => i !== index) })}><Trash2 size={15} /></button></div>
        <div className="factory-row"><label>字段类型<select value={field.type} onChange={event => updateField(index, {
          type: event.target.value as InputField['type'], default: event.target.value === 'number' ? 0 : event.target.value === 'boolean' ? false : '',
        })}><option value="text">文本</option><option value="number">数值</option><option value="boolean">开关</option></select></label>
          <FieldInput field={{ ...field, label: '默认值', required: false }} value={field.default} onChange={value => updateField(index, { default: value })} /></div>
        <label className="factory-check"><input type="checkbox" checked={field.required} onChange={event => updateField(index, { required: event.target.checked })} />必填</label>
      </div>)}
      <button type="button" disabled={draft.fields.length >= 20} onClick={() => {
        let index = draft.fields.length + 1; while (draft.fields.some(field => field.key === `field${index}`)) index++;
        change({ fields: [...draft.fields, { key: `field${index}`, label: `字段 ${index}`, type: 'text', default: '', required: false }] });
      }}><Plus size={14} />添加字段</button>
      <label>运行方式<select value={draft.operation} onChange={event => change({ operation: event.target.value as FunctionDesign['operation'] })}>
        <option value="template">填充文本模板</option><option value="sum">数值字段求和</option><option value="multiply">数值字段乘积</option><option value="join">按字段顺序拼接</option></select></label>
      {draft.operation === 'template' && <label>输出模板<textarea rows={4} maxLength={10000} value={draft.template} onChange={event => change({ template: event.target.value })} /></label>}
      {draft.operation === 'join' && <label>分隔符<input maxLength={100} value={draft.separator} onChange={event => change({ separator: event.target.value })} /></label>}
      <SaveButton editor={editor} task={task} />
    </div><div className="factory-test"><h4>功能试验台</h4><FunctionForm design={draft}
      run={async values => (await worldApi.factory<{ result: Scalar }>(props.card.id, 'try', { design: draft, values })).result} /></div></div>
  </Frame>;
}

function useInputs(cardId: string) {
  const [context, setContext] = useState<FactoryContext>();
  const edges = useWorldStore(state => state.edges), cards = useWorldStore(state => state.cards);
  const signature = edges.filter(edge => edge.target === cardId).map(edge => `${edge.id}:${cards.find(card => card.id === edge.source)?.revision}`).join('|');
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    void worldApi.factory<FactoryContext>(cardId, 'context').then(value => { if (current) { setContext(value); setError(''); } })
      .catch(cause => { if (current) setError(apiErrorMessage(cause)); });
    return () => { current = false; };
  }, [cardId, signature]);
  return { context, error };
}

function Inputs({ context, error }: ReturnType<typeof useInputs>) {
  return <div className="factory-inputs">{error && <p role="alert">{error}</p>}
    {!context && !error && <p>正在读取连线…</p>}
    {context?.issues.map(issue => <p key={issue}>{issue}</p>)}
    {context && Object.entries(context.inputs).map(([key, input]) => <span key={key}>● {input.name}</span>)}
  </div>;
}

export function Printer(props: PluginViewProps) {
  const task = useTask(), inputs = useInputs(props.card.id), [printed, setPrinted] = useState<WorldCard>();
  if (props.level === 'preview') return <ToolPreview id={props.card.id}><strong>{inputs.context && !inputs.context.issues.length ? '已就绪，可以印刷' : '等待设计连线'}</strong><small>卡面 + 功能 → 预设卡牌</small></ToolPreview>;
  return <Frame title="卡牌印刷器" step="04 / 印刷" task={task} level={props.level}>
    <Inputs {...inputs} />
    {inputs.context?.inputs.face && <FacePreview face={inputs.context.inputs.face.config as FaceDesign} />}
    <p>印刷会固定当前已保存的卡面和功能。修改设计后可再次印刷。</p>
    <button type="button" disabled={!inputs.context || inputs.context.issues.length > 0} onClick={() => void task.perform(async () => {
      const card = await worldApi.factory<WorldCard>(props.card.id, 'print');
      await useWorldStore.getState().refreshWorld(); setPrinted(card); task.setMessage(`已印刷「${card.name}」，可拖入打包器。`);
    })}><PrinterIcon size={16} />印刷卡牌</button>
    {printed && <button type="button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(printed.id)}>打开「{printed.name}」</button>}
  </Frame>;
}

export async function addFactoryItem(packerId: string, item: BasketItem) {
  await worldApi.factory(packerId, 'items', item);
  await useWorldStore.getState().refreshWorld();
}

function downloadFile(file: File) {
  const url = URL.createObjectURL(file), link = document.createElement('a');
  link.href = url; link.download = file.name; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Packer(props: PluginViewProps) {
  const task = useTask(), inputs = useInputs(props.card.id);
  const config = props.card.config as unknown as PackerConfig;
  type ParamRow = { key: string; value: Scalar; type: InputField['type'] };
  const optionsDraft = props.host.draft?.get()?.factory_options as { rows: ParamRow[]; includeContent: boolean } | undefined;
  const [params, setParams] = useState<ParamRow[]>(() => optionsDraft?.rows ?? Object.entries(config.params).map(([key, value]) => ({
    key, value, type: typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'text',
  })));
  const [includeContent, setIncludeContent] = useState(optionsDraft?.includeContent ?? config.include_content);
  useEffect(() => {
    props.host.draft?.set({ factory_options: { rows: params, includeContent } });
  }, [params, includeContent, props.host.draft]);
  const [selection, setSelection] = useState(''), [legions, setLegions] = useState<LegionSummary[]>([]);
  const [inspection, setInspection] = useState<FactoryInspection>(), [file, setFile] = useState<File>();
  const cards = useWorldStore(state => state.cards), catalog = useWorldStore(state => state.catalog);
  const signature = JSON.stringify({ config, inputs: inputs.context, params, includeContent, sources: config.items.map(item =>
    item.kind === 'node' ? cards.find(card => card.id === item.id)?.revision : legions.find(legion => legion.id === item.id)?.revision) });
  useEffect(() => { setInspection(undefined); setFile(undefined); }, [signature]);
  useEffect(() => { void worldApi.getLegions().then(setLegions).catch(() => {}); }, []);
  const choices: BasketItem[] = [...cards.filter(card => !card.ephemeral && (!card.type.startsWith('oaw.factory.') || card.type === 'oaw.factory.card') && catalog.node_types.find(type => type.id === card.type)?.templateable)
    .map(card => ({ id: card.id, name: card.name, kind: 'node' as const })),
    ...legions.map(legion => ({ id: legion.id, name: legion.name, kind: legion.preset ? 'preset' as const : 'legion' as const }))];
  const saveOptions = async () => {
    if (params.some(param => !/^[a-z][a-z0-9_]{0,39}$/.test(param.key)) || new Set(params.map(param => param.key)).size !== params.length)
      throw new Error('参数标识需唯一，以小写字母开头，仅包含字母、数字和下划线。');
    const next = { ...config, params: Object.fromEntries(params.map(param => [param.key, param.value])), include_content: includeContent };
    if (JSON.stringify(next) !== JSON.stringify(config)) await props.host.updateConfig(next);
  };
  const createExport = async () => {
    await saveOptions();
    const result = await worldApi.factory<FactoryInspection>(props.card.id, 'inspect'); setInspection(result);
    if (!result.can_export) throw new Error('请先解决发布检查中的错误。');
    const blob = await worldApi.factory<Blob>(props.card.id, 'export');
    return new File([blob], `${result.manifest.id}-${result.manifest.version}.oawpack`, { type: 'application/vnd.oaw.pack' });
  };
  if (props.level === 'preview') return <ToolPreview id={props.card.id}><div className="factory-dropzone" data-factory-packer={props.card.id}>
    <Package size={18} /><strong>拖入卡牌 / Legion</strong><small>{config.items.length} 份素材</small></div></ToolPreview>;
  return <Frame title="打包器" step="05 / 发布" task={task} level={props.level}>
    <Inputs {...inputs} />
    <div className="factory-dropzone" data-factory-packer={props.card.id}><Package size={25} /><strong>将卡牌或 Legion 拖到这里</strong>
      <small>保留原件；导出时复制当前配置。</small>
      {config.items.length === 0 && <p>还没有素材，试着印刷第一张卡。</p>}
      <ul>{config.items.map(item => <li key={`${item.kind}:${item.id}`}><span>{item.name}<small>{item.kind === 'node' ? '画布卡牌' : 'Legion 模板'}</small></span>
        <button type="button" aria-label={`移除 ${item.name}`} onClick={() => void task.perform(async () => {
          await props.host.updateConfig({ ...config, items: config.items.filter(value => value !== item) });
        })}><Trash2 size={14} /></button></li>)}</ul>
    </div>
    <div className="factory-row"><label>选择素材<select value={selection} onChange={event => setSelection(event.target.value)}><option value="">选择卡牌或已保存的 Legion</option>
      {choices.filter(choice => !config.items.some(item => item.id === choice.id && item.kind === choice.kind)).map(choice => <option key={`${choice.kind}:${choice.id}`} value={`${choice.kind}:${choice.id}`}>{choice.name} · {choice.kind === 'node' ? '画布' : 'Legion'}</option>)}</select></label>
      <button type="button" disabled={!selection || config.items.length >= 50} onClick={() => void task.perform(async () => {
        const item = choices.find(choice => `${choice.kind}:${choice.id}` === selection); if (item) await addFactoryItem(props.card.id, item); setSelection('');
      })}><Plus size={14} />加入</button>
      <button type="button" aria-label="刷新素材" onClick={() => void task.perform(async () => { await useWorldStore.getState().refreshWorld(); setLegions(await worldApi.getLegions()); })}><RefreshCw size={14} /></button></div>
    <details open><summary>通用参数</summary><p className="factory-hint">覆盖自定义卡牌中同名字段的默认值。原卡牌保持原来的值。</p>
      {params.map((param, index) => <div key={index} className="factory-row"><label>参数标识<input maxLength={40} value={param.key} onChange={event => setParams(current => current.map((p, i) => i === index ? { ...p, key: event.target.value } : p))} /></label>
        <label>类型<select value={param.type} onChange={event => setParams(current => current.map((p, i) => i === index ? { ...p, type: event.target.value as InputField['type'], value: event.target.value === 'number' ? 0 : event.target.value === 'boolean' ? false : '' } : p))}>
          <option value="text">文本</option><option value="number">数值</option><option value="boolean">开关</option></select></label>
        <FieldInput field={{ key: param.key, label: '参数值', type: param.type, default: param.value, required: false }} value={param.value}
          onChange={value => setParams(current => current.map((p, i) => i === index ? { ...p, value } : p))} />
        <button type="button" aria-label={`删除参数 ${index + 1}`} onClick={() => setParams(current => current.filter((_, i) => i !== index))}><Trash2 size={14} /></button></div>)}
      <button type="button" disabled={params.length >= 30} onClick={() => setParams(current => [...current, { key: '', value: '', type: 'text' }])}><Plus size={14} />添加参数</button>
    </details>
    <label className="factory-check"><input type="checkbox" checked={includeContent} onChange={event => setIncludeContent(event.target.checked)} />包含素材中的初始文档、资源与共享状态</label>
    <small>默认只携带配置；勾选后请检查内容是否适合公开分享。</small>
    <div className="factory-actions"><button type="button" onClick={() => void task.perform(async () => { await saveOptions(); task.setMessage('打包设置已保存。'); })}><Save size={14} />保存设置</button>
      <button type="button" disabled={!config.items.length} onClick={() => void task.perform(async () => { await saveOptions(); setInspection(await worldApi.factory(props.card.id, 'inspect')); })}>检查卡包</button>
      <button type="button" disabled={!config.items.length} onClick={() => void task.perform(async () => { const exported = await createExport(); setFile(exported); downloadFile(exported); task.setMessage('已导出，可发布到自己的仓库或发送给朋友。'); })}><Download size={14} />导出 .oawpack</button></div>
    {inspection && <div className="factory-review"><strong>{inspection.can_export ? '可以导出' : '需要修正'} · {inspection.entries.length} 份素材</strong>
      {inspection.issues.map((issue, index) => <p key={index} className={issue.severity === 'error' ? 'factory-error' : ''}>{issue.path}：{issue.message}</p>)}
      <small>依赖卡包</small><ul>{inspection.manifest.dependencies?.packs.map(dep => <li key={dep.id}>{dep.id} {dep.version}</li>)}</ul></div>}
    {file && <div className="factory-actions"><button type="button" onClick={() => downloadFile(file)}>再次下载</button>
      <button type="button" onClick={() => void task.perform(async () => {
        if (navigator.canShare?.({ files: [file] })) { await navigator.share({ files: [file], title: file.name }); task.setMessage('已完成分享。'); }
        else { downloadFile(file); task.setMessage('当前环境不支持系统分享，已下载文件，发送此文件即可。'); }
      })}><Share2 size={14} />分享文件</button></div>}
  </Frame>;
}
