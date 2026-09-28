import { t } from "@oaw/plugin-api";

export const evidenceKinds = {author_statement:"作者主张",agent_inference:"Agent 推断",user_hypothesis:"用户假设",research_fact:"研究事实（既有分类）"} as const;
export const evidenceRelations = {supports:"支持",contradicts:"相悖",insufficient:"证据不足"} as const;
export type EvidenceKind = keyof typeof evidenceKinds;
export type EvidenceRelation = keyof typeof evidenceRelations;

export function EvidenceClassification({kind,relation,onKind,onRelation,disabled=false}:{kind:EvidenceKind;relation:EvidenceRelation;onKind:(value:EvidenceKind)=>void;onRelation:(value:EvidenceRelation)=>void;disabled?:boolean}) {
  return <div className="literature-evidence-classification">
    <label>{t("记录类型")}<select disabled={disabled} value={kind} onChange={event=>onKind(event.target.value as EvidenceKind)}>{Object.entries(evidenceKinds).filter(([key])=>key!=="research_fact"||kind==="research_fact").map(([key,label])=><option key={key} value={key}>{t(label)}</option>)}</select></label>
    <label>{t("原文与主张的关系")}<select disabled={disabled} value={relation} onChange={event=>onRelation(event.target.value as EvidenceRelation)}>{Object.entries(evidenceRelations).map(([key,label])=><option key={key} value={key}>{t(label)}</option>)}</select></label>
  </div>;
}
