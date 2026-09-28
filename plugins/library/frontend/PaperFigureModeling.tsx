import { t, useLocale } from "@oaw/plugin-api";
import { useState } from "react";
import { apiErrorMessage, worldApi } from "../../../frontend/src/api/client";
import { modelRef } from "../../../frontend/src/state/modelConnections";
import { useNodeSurfaceStore } from "../../../frontend/src/state/nodeSurfaces";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import type { Annotation } from "./PdfReading";
import "./paperFigureModeling.css";

function FigureIcon() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8"/></svg>; }
function CopyIcon({ copied }: { copied: boolean }) { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">{copied ? <path d="m5 12 4 4L19 6"/> : <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></>}</svg>; }

type FigureModelingResult = {
  paper_id: string; image_id: string; agent_id: string; structure_id: string;
  model: string; replay: boolean; workspace_reused?: boolean; run_prompt: string;
};

function responseError(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const error = value as Record<string, unknown>;
    for (const key of ["detail", "error", "message"]) if (error[key]) return responseError(error[key]);
  }
  return t("创建建模工作区失败，请重试。");
}

/** The backend recrops the saved PDF region; the thumbnail is never submitted as evidence. */
export function PaperFigureModeling({ paperId, documentVersionId, annotation, saved, onOpenChange }: {
  paperId: string; documentVersionId?: string; annotation: Annotation;
  saved: boolean; onOpenChange?: (open: boolean) => void;
}) {
  useLocale();
  const catalog = useWorldStore(state => state.modelCatalog);
  const cards = useWorldStore(state => state.cards);
  const scopes = cards.filter(card => card.type === "literature.scope");
  const sharedAgent = cards.find(card => card.type === "atomsculptor.agent" && card.config?.paper_id === paperId && card.config?.research_projection === "paper_modeling");
  const choices = catalog.connections.filter(connection => connection.enabled).flatMap(connection =>
    connection.models.filter(model => model.enabled && model.supports_images === true).map(model => ({
      value: modelRef(model.id), label: `${connection.name} / ${model.name}`,
    })));
  const [selectedModel, setSelectedModel] = useState(catalog.default_model ?? "");
  const model = sharedAgent ? String(sharedAgent.config.model ?? "") : choices.some(choice => choice.value === selectedModel) ? selectedModel : choices[0]?.value ?? "";
  // The active map scope does not establish that this Paper belongs to it.
  const [scopeId, setScopeId] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<FigureModelingResult>();
  const [copied, setCopied] = useState(false);
  const currentVersion = Boolean(documentVersionId && (!annotation.document_version_id || annotation.document_version_id === documentVersionId));
  const validCrop = annotation.rects.length === 1 && annotation.rects[0].length === 4 && annotation.rects[0][2] > 0 && annotation.rects[0][3] > 0;

  function openWorkspace(created: FigureModelingResult) {
    useWorldStore.getState().selectCards([created.structure_id, created.agent_id], { syncCanvas: true });
    useNodeSurfaceStore.getState().openWorkspace(created.structure_id);
    useNodeSurfaceStore.getState().openInspector(created.agent_id);
  }

  async function create() {
    if (busy || !saved || !currentVersion || !validCrop || !model) return;
    setBusy(true); setError("");
    try {
      const paper = await worldApi.getNodeDocumentSummary(paperId);
      const response = await fetch(`/api/literature/papers/${encodeURIComponent(paperId)}/model_figure`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: paper.revision, arguments: {
          annotation_id: annotation.id, document_version_id: documentVersionId, model,
          ...(scopeId ? { scope_id: scopeId } : {}),
        } }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(responseError(payload));
      const created = payload as FigureModelingResult;
      setResult(created);
      await useWorldStore.getState().refreshWorld();
      openWorkspace(created);
      if (scopeId) window.dispatchEvent(new CustomEvent("oaw-research-updated", { detail: scopeId }));
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }

  return <section className={`library-figure-modeling ${open ? "is-open" : ""}`}>
    <button type="button" aria-expanded={open} onClick={() => { setOpen(!open); onOpenChange?.(!open); }}><FigureIcon/>{t("用此图建模")}</button>
    {open && <div>
      <p>{t("每张文献图单独保存；同一篇论文共用一个建模 Agent 和 3D 画布。")}</p>
      {annotation.image && <img src={annotation.image} alt={t("选中的文献图")}/>}
      {!currentVersion ? <p role="alert">{t("此摘录不属于当前 PDF 版本，请重新框选原文图片。")}</p> : !validCrop ? <p role="alert">{t("请在 PDF 中框选一个连续的图片区域。")}</p> : <small>{t("Page {page}", { page: annotation.page })} · {t("来源绑定到当前 PDF 版本")}</small>}
      {!result ? <>
        <label>{t("建模模型")}<select value={model} disabled={busy || !choices.length || Boolean(sharedAgent)} onChange={event => setSelectedModel(event.target.value)} aria-label={t("建模模型")}>
          {!choices.length && <option value="">{t("暂无支持图像输入的模型")}</option>}
          {choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        </select></label>
        {!choices.length && <small>{t("请在 OAW 模型设置中配置支持图像输入的模型，并启用 Vision。")}</small>}
        <label>{t("研究范围（可选）")}<select value={scopeId} disabled={busy} onChange={event => setScopeId(event.target.value)}>
          <option value="">{t("仅关联这篇文献")}</option>
          {scopes.map(scope => <option key={scope.id} value={scope.id}>{scope.name}</option>)}
        </select></label>
        <small>{t("生成结果是待核对的结构草稿；图中未给出的晶胞或坐标需要补充依据。")}</small>
        <button type="button" disabled={busy || !saved || !model || !currentVersion || !validCrop} onClick={() => void create()}>
          {busy ? t("正在准备…") : !saved ? t("等待截图保存…") : sharedAgent ? t("添加文献图并复用工作区") : t("创建建模工作区")}
        </button>
      </> : <>
        <p role="status">{result.workspace_reused ? t("已复用这篇论文的 Agent 和 3D 画布，当前任务使用所选文献图。") : result.replay ? t("已打开此图已有的建模工作区。") : t("已在文献旁创建建模工作区。")}</p>
        <small>{t("任务已准备好。可复制任务，在建模 Agent 中启动。")}</small>
        <textarea readOnly aria-label={t("建模任务")} value={result.run_prompt}/>
        <div className="library-figure-modeling-actions">
          <button type="button" onClick={() => openWorkspace(result)}><FigureIcon/>{t("打开建模工作区")}</button>
          <button type="button" title={t("复制建模任务")} aria-label={t("复制建模任务")} onClick={() => void navigator.clipboard.writeText(result.run_prompt).then(() => setCopied(true)).catch(reason => setError(apiErrorMessage(reason)))}><CopyIcon copied={copied}/></button>
        </div>
      </>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </section>;
}
