import {useEffect,useState} from 'react';
import {t} from '@oaw/plugin-api';
import type {ResearchScopeDoc} from './index';

export function researchError(value:unknown):string {
  if(typeof value === 'object' && value) {
    const data=value as Record<string,unknown>;
    return researchError(data.detail ?? data.error ?? data.message ?? JSON.stringify(data));
  }
  const message=String(value ?? '');
  try { if(message.startsWith('{'))return researchError(JSON.parse(message)); } catch { /* plain message */ }
  if(/duration budget exhausted|time budget is exhausted/i.test(message))return t('检索时间窗口已结束。请打开范围与预算，审阅并保存新版本后继续；已有文献和记录会保留。');
  if(/budget.*exhaust|budget.*exceed|candidate.*budget/i.test(message))return t('本轮检索次数或候选名额已用完。请审阅范围与预算后继续，历史记录会保留。');
  return message;
}

export function ResearchBudgetStatus({doc,onReview}:{doc:ResearchScopeDoc;onReview:()=>void}) {
  const [clock,setClock]=useState(Date.now());
  useEffect(()=>{const timer=window.setInterval(()=>setClock(Date.now()),15000);return()=>window.clearInterval(timer);},[]);
  const budget=doc.value.search_budgets[String(doc.value.current_revision)];
  const ledger=budget as {max_searches:number;max_candidate_slots:number;deadline?:string;reservations:{candidate_slots?:number;attempts?:number}[]} | undefined;
  if(!ledger)return null;
  const deadline=ledger.deadline ? Date.parse(ledger.deadline) : undefined;
  const slots=ledger.reservations.reduce((sum,item)=>sum+(item.candidate_slots ?? 0),0);
  const expired=deadline!==undefined && deadline<=clock;
  const runs=doc.value.search_runs.filter(run=>run.scope_revision===doc.value.current_revision);
  const added=new Set(runs.flatMap(run=>run.paper_ids)).size;
  return <div className="literature-budget-status" role="status">
    <small>{t('本轮预约')} {ledger.reservations.length}/{ledger.max_searches} · {t('候选名额')} {slots}/{ledger.max_candidate_slots} · {t('所得文献')} {added}</small>
    {deadline!==undefined && <small>{expired?t('检索时间窗口已结束'): `${t('时间窗口剩余')} ${Math.max(0,Math.ceil((deadline-clock)/60000))} ${t('分钟')}`}</small>}
    {(expired || ledger.reservations.length>=ledger.max_searches || slots>=ledger.max_candidate_slots) && <button type="button" onClick={onReview}>{t('审阅预算并续期')}</button>}
    <small>{t('预约名额包含失败尝试；续期会建立范围新版本并保留历史记录。')}</small>
  </div>;
}
