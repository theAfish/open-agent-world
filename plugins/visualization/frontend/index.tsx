import { NetworkMap, type NetworkData } from "@oaw/plugin-api";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocale, type DataSchema, type Dataset, type FrontendPlugin, type PluginViewProps } from '@oaw/plugin-api';
import { ChartCanvas } from './ChartCanvas';
import { preparePlot, type Kind } from './plot';
import './style.css';

interface Config { source_id: string; schema_id: string; x: string; y: string; series: string;
  aggregate: 'none' | 'count' | 'sum' | 'mean' | 'min' | 'max'; limit: number; entity_type: string; relation_type: string }
const defaults: Config = {source_id:'',schema_id:'',x:'',y:'',series:'',aggregate:'none',limit:2000,entity_type:'',relation_type:''};
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

export function Visualization({card, host}: PluginViewProps) {
  const {locale} = useLocale(), zh = locale === 'zh-CN';
  const label = (en: string, cn: string) => zh ? cn : en;
  const kind = card.type.split('.').pop() as Kind;
  const config = {...defaults, ...card.config} as Config;
  const [sources, setSources] = useState<{id:string;name:string}[]>([]), [available,setAvailable] = useState<{id:string;name:string}[]>([]);
  const [schemas, setSchemas] = useState<DataSchema[]>([]), [data,setData] = useState<Dataset | null>(null);
  const [error,setError] = useState(''), [loading,setLoading] = useState(false), [saving,setSaving] = useState(false), [revision,refresh] = useState(0);
  const [schemaBusy,setSchemaBusy] = useState(false), [schemaSource,setSchemaSource] = useState('');
  const request = useRef(0), schemaRequest = useRef(0), sourceRequest = useRef(0);
  const api = host.dataSources;
  const sourceId = config.source_id || (sources.length === 1 ? sources[0].id : '');
  const connected = sources.some(item=>item.id===sourceId);
  const schema = schemaSource === sourceId ? schemas.find(item=>item.id===config.schema_id) : undefined;
  const compatible = schema?.kind === 'table' || (kind === 'graph' && schema?.kind === 'graph');
  const fields = schema?.fields ?? [];
  const fieldNames = new Set(fields.map(f=>f.name));
  const configured = connected && compatible && (schema?.kind === 'graph' || (
    (kind === 'histogram' || fieldNames.has(config.x)) && (config.aggregate === 'count' || fieldNames.has(config.y)) && (!config.series || fieldNames.has(config.series))));
  const invalidate = () => { request.current++; setData(null); setError(''); };
  const save = async (patch: Partial<Config>) => {
    invalidate(); setSaving(true);
    try { await host.updateConfig(patch); } catch(e) {setError(message(e));} finally {setSaving(false);}
  };
  const reloadSources = useCallback(async () => {
    if (!api) return;
    const id = ++sourceRequest.current; request.current++; setData(null); setLoading(false); setError('');
    try {
      const [linked, all] = await Promise.all([api.list(), host.listCards(['data.source'])]);
      if(id !== sourceRequest.current) return;
      setSources(linked.sources); setAvailable(all); refresh(n=>n+1);
    } catch(e) {if(id===sourceRequest.current)setError(message(e));}
  }, [api, host.listCards]);
  useEffect(() => {void reloadSources(); const unsubscribe = api?.subscribe(()=>void reloadSources()); return ()=>{unsubscribe?.(); sourceRequest.current++;};}, [api,reloadSources]);
  useEffect(() => {
    const id = ++schemaRequest.current; setSchemas([]); setSchemaSource(''); setData(null); request.current++;
    if (!api || !sourceId || !connected) {setSchemaBusy(false); return;}
    setSchemaBusy(true); setError('');
    api.schemas(sourceId).then(result=>{if(id===schemaRequest.current){setSchemas(result.schemas);setSchemaSource(sourceId);}})
      .catch(e=>{if(id===schemaRequest.current)setError(message(e));}).finally(()=>{if(id===schemaRequest.current)setSchemaBusy(false);});
    return ()=>{schemaRequest.current++;};
  }, [api,sourceId,connected,revision]);
  useEffect(() => {
    const id = ++request.current; setData(null); setLoading(false);
    if(!api || !configured || saving) return;
    setLoading(true); setError('');
    const aggregate = schema?.aggregates && kind !== 'graph' && kind !== 'histogram' ? config.aggregate : 'none';
    const columns = schema?.kind === 'graph' ? [] : [...new Set([...(kind==='histogram'?[]:[config.x]), ...(aggregate==='count'?[]:[config.y]), ...(config.series&&aggregate==='none'?[config.series]:[])].filter(Boolean))];
    const query = {schema_id:config.schema_id,columns,limit:config.limit,aggregate,
      ...(aggregate!=='none'?{group_by:config.x,value:config.y}:{}),
      ...(kind==='line'?{order_by:config.x}:{}),
      ...(config.entity_type?{entity_type:config.entity_type}:{}), ...(config.relation_type?{relation_type:config.relation_type}:{}),
    };
    api.read(sourceId,query).then(result=>{if(id===request.current)setData(result);})
      .catch(e=>{if(id===request.current)setError(message(e));}).finally(()=>{if(id===request.current)setLoading(false);});
    return ()=>{request.current++;};
  },[api,sourceId,config.schema_id,config.x,config.y,config.series,config.aggregate,config.limit,config.entity_type,config.relation_type,configured,saving,schema]);
  const plot = useMemo(()=>data?preparePlot(data,kind,config):null,[data,kind,config.x,config.y,config.series,config.aggregate]);
  const network = useMemo<NetworkData>(() => {
    const counts = new Map<string, number>();
    return { nodes: (plot?.nodes ?? []).map(n => ({ id: n.id, label: n.name, kind: n.type })),
      edges: (plot?.edges ?? []).map(e => { const key = JSON.stringify([e.source, e.target, e.type ?? '']); const count = counts.get(key) ?? 0; counts.set(key, count + 1);
        return { id: e.id ?? `${key}:${count}`, source: e.source, target: e.target, label: e.type }; }) };
  }, [plot]);
  const pickSource = async (id: string) => {
    if (!id) return;
    setSaving(true);
    try {
      await api?.connect(id,'data.visualization.source');
    } catch(e) {setError(message(e));} finally {setSaving(false);}
  };
  const pickSchema = (id: string) => {
    const next = schemas.find(s=>s.id===id), numeric = next?.fields.find(f=>f.type==='number')?.name ?? '';
    const x = next?.fields.find(f=>f.name!==numeric)?.name ?? next?.fields[0]?.name ?? '';
    void save({source_id:sourceId,schema_id:id,x,y:kind==='graph'?(next?.fields.find(f=>f.name!==x)?.name??''):numeric,series:'',aggregate:'none',entity_type:'',relation_type:''});
  };
  const select = (key: 'x'|'y'|'series', title: string, numericOnly = false) => <label><span>{title}</span><select aria-label={title} value={config[key]} disabled={saving} onChange={e=>void save({[key]:e.target.value})}>
    <option value="">{key==='series'?label('None','无'):label('Choose field','选择字段')}</option>
    {config[key]&&!fieldNames.has(config[key])&&<option value={config[key]}>{config[key]} ({label('missing','已不存在')})</option>}
    {fields.filter(f=>!numericOnly||f.type==='number').map(f=><option key={f.name} value={f.name}>{f.name}</option>)}
  </select></label>;
  return <section className="viz-app nodrag nopan nowheel" aria-label={label('Data visualization','数据可视化')}>
    <div className="viz-toolbar">
      <label><span>{label('Source','数据源')}</span><select aria-label={label('Data source','数据源')} value={sourceId} disabled={saving||!api} onChange={e=>void pickSource(e.target.value)}>
        <option value="">{label('Connect a database…','连接数据库…')}</option>
        {config.source_id&&!available.some(s=>s.id===config.source_id)&&<option value={config.source_id}>{label('Source unavailable','数据源不可用')}</option>}
        {available.map(s=><option key={s.id} value={s.id}>{s.name}{sources.some(v=>v.id===s.id)?'':` · ${label('connect','连接')}`}</option>)}
      </select></label>
      <label><span>Schema</span><select aria-label="Schema" value={config.schema_id} disabled={saving||!connected||schemaBusy} onChange={e=>pickSchema(e.target.value)}>
        <option value="">{schemaBusy?label('Loading…','加载中…'):label('Choose schema…','选择 schema…')}</option>
        {config.schema_id&&!schemas.some(s=>s.id===config.schema_id)&&<option value={config.schema_id}>{config.schema_id} ({label('unavailable','不可用')})</option>}
        {schemas.filter(s=>kind==='graph'||s.kind==='table').map(s=><option key={s.id} value={s.id}>{s.label}</option>)}
      </select></label>
      <button title={label('Refresh data','刷新数据')} aria-label={label('Refresh data','刷新数据')} disabled={saving||loading||schemaBusy} onClick={()=>void reloadSources()}>↻</button>
      {!connected&&sourceId&&available.some(s=>s.id===sourceId)&&<button disabled={saving} onClick={()=>void pickSource(sourceId)}>{label('Reconnect','重新连接')}</button>}
    </div>
    {schema?.kind==='table'&&<div className="viz-fields">
      {kind!=='histogram'&&select('x',kind==='graph'?label('From','起点'):'X')}
      {config.aggregate!=='count'&&select('y',kind==='graph'?label('To','终点'):kind==='histogram'?label('Value','数值'):'Y')}
      {kind!=='histogram'&&config.aggregate==='none'&&select('series',kind==='graph'?label('Relation','关系'):label('Series','分组'))}
      {schema.aggregates&&kind!=='graph'&&kind!=='histogram'&&<label><span>{label('Aggregate','统计')}</span><select aria-label={label('Aggregate','统计')} value={config.aggregate} disabled={saving} onChange={e=>void save({aggregate:e.target.value as Config['aggregate']})}>
        {(['none','count','sum','mean','min','max'] as const).map((v,i)=><option key={v} value={v}>{label(['Raw rows','Count','Sum','Mean','Minimum','Maximum'][i],['原始数据','计数','求和','平均','最小','最大'][i])}</option>)}
      </select></label>}
    </div>}
    {schema?.kind==='graph'&&<div className="viz-fields">
      <label><span>{label('Entity type','实体类型')}</span><input aria-label={label('Entity type','实体类型')} key={`entity:${config.schema_id}:${sourceId}`} placeholder={label('All','全部')} defaultValue={config.entity_type} onBlur={e=>{if(e.target.value!==config.entity_type)void save({entity_type:e.target.value});}} /></label>
      <label><span>{label('Relation type','关系类型')}</span><input aria-label={label('Relation type','关系类型')} key={`relation:${config.schema_id}:${sourceId}`} placeholder={label('All','全部')} defaultValue={config.relation_type} onBlur={e=>{if(e.target.value!==config.relation_type)void save({relation_type:e.target.value});}} /></label>
    </div>}
    <div className="viz-stage">
      {error?<div className="viz-empty" role="alert">{error}<button onClick={()=>void reloadSources()}>{label('Retry','重试')}</button></div>:
        loading||schemaBusy?<div className="viz-empty" role="status">{label('Loading data…','读取数据中…')}</div>:
        plot&&(plot.points.length||plot.nodes.length)?kind==='graph'?<NetworkMap graphKey={`visualization:${card.id}`} data={network} label="Graph visualization" />:<ChartCanvas plot={plot} kind={kind} xLabel={kind==='histogram'?config.y:config.x} yLabel={kind==='histogram'?label('Count','数量'):config.aggregate==='count'?label('Count','数量'):config.y}/>:
        <div className="viz-empty"><span className="viz-empty-icon">{kind==='graph'?'⌘':'▥'}</span>{!connected?label('Connect a data source to begin','连接数据源，开始可视化'):!schema?label('Choose a schema to visualize','选择需要展示的 schema'):!configured?label('Choose fields to plot','选择需要绘制的字段'):label('No plottable data','暂无可绘制的数据')}</div>}
    </div>
    <footer className="viz-status"><span role="status">{data?data.kind==='graph'?`${data.nodes?.length??0} ${label('nodes','节点')} · ${data.edges?.length??0} ${label('edges','关系')}`:`${data.rows?.length??0} ${label('rows','行')}${data.scope==='full'?` · ${label('Full-data aggregate','全量统计')}`:''}`:label('Read-only','只读')}
      {data?.truncated&&<strong> · {label('Partial data','部分数据')}</strong>}{plot?.skipped?` · ${plot.skipped} ${label('empty / invalid skipped','空值或无效值已跳过')}`:''}</span>
      <label>{label('Limit','上限')} <select aria-label={label('Row limit','数据上限')} value={config.limit} disabled={saving} onChange={e=>void save({limit:Number(e.target.value)})}>{[500,2000,10000].map(n=><option key={n} value={n}>{n.toLocaleString()}</option>)}</select></label>
    </footer>
  </section>;
}

function Preview({card}: PluginViewProps) { return <div className="viz-preview">▥<span>{String(card.config.schema_id||card.name)}</span></div>; }
export default {apiVersion:1,views:{chart:Visualization,preview:Preview}} satisfies FrontendPlugin;
