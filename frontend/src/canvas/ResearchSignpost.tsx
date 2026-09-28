import { memo, useRef } from 'react';
import { Handle, Position, useStore, type NodeProps } from '@xyflow/react';
import { Flag } from 'lucide-react';
import type { CanvasNode } from '../cards/types';
import { useAutoResearch } from '../state/autoResearch';
import { useExplorationScope } from '../../../plugins/literature/frontend/Exploration';
import { discoveryLabel } from '../../../plugins/literature/frontend/ScopeFrontiers';
import { t, useLocale } from '../i18n';

/** A real draggable node, retaining the existing route identity and geometry. */
export const ResearchSignpost = memo(function ResearchSignpost({data,selected}:NodeProps<CanvasNode>) {
  useLocale();
  const card=data.card;
  const pointer=useRef({x:0,y:0});
  const far=useStore(state=>state.transform[2]<.45);
  const {doc}=useExplorationScope(String(card.config.scope_id ?? ''));
  const route=doc?.value.frontiers.find(route=>route.id===card.config.frontier_id || `trail:${route.id}`===card.config.entity_id);
  const road=doc?.value.exploration_roads?.find(road=>road.frontier_id===route?.id);
  const detailOpen=useAutoResearch(state=>state.panel==='directions' && state.scopeId===card.config.scope_id && state.selectedFrontierId===(route?.id ?? card.config.frontier_id ?? undefined));
  const open=()=>{
    const state=useAutoResearch.getState(),scopeId=String(card.config.scope_id ?? '');
    if(state.scopeId!==scopeId) state.selectScope(scopeId);
    const frontierId=route?.id ?? card.config.frontier_id;
    if(typeof frontierId==='string') useAutoResearch.getState().selectFrontier(frontierId);
    else useAutoResearch.setState({selectedFrontierId:undefined,panel:'directions'});
  };
  return <div className="research-signpost" data-selected={selected} data-detail-open={detailOpen || undefined} data-far={far || undefined} aria-label={card.name}>
    {([[Position.Top,'top'],[Position.Right,'right'],[Position.Bottom,'bottom'],[Position.Left,'left']] as const).map(([position,side])=><Handle key={side} id={`boundary-${side}`} type="source" position={position} className={`semantic-handle semantic-handle--${side}`}/>)}
    <div className="research-signpost-art" role="button" tabIndex={0} aria-label={`${card.name} · ${t("路标")}`} onPointerDown={event=>{pointer.current={x:event.clientX,y:event.clientY};}} onClick={event=>{if(Math.hypot(event.clientX-pointer.current.x,event.clientY-pointer.current.y)<5) open();}} onKeyDown={event=>{if(event.key==='Enter' || event.key===' '){event.preventDefault();open();}}}><Flag className="research-signpost-far"/><span className="research-fog-sprite"/></div>
    <button className="research-signpost-title nodrag nopan" onClick={open} title={card.name}>
      <small>{card.config.entity_id==='trail:origin' ? t('主干道起点') : road?.mode==='branch' ? t('分叉研究') : t('递进研究')} · {route ? t(discoveryLabel(route.discovery_state)) : t('探索方向')}</small>
      <strong>{card.name}</strong><span>{t('打开路径详情')}</span>
    </button>
  </div>;
});
