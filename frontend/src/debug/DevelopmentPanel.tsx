import { useEffect, useRef, useState } from "react";
import { Bug, X, RotateCcw, LoaderCircle } from "lucide-react";
import { useLocale } from "../i18n";
import { currentProfile, flushPreferences } from "../state/profileStorage";
import { useWorldStore } from "../state/worldStore";
import "./development.css";

type Scope = "workspace" | "decks" | "packs" | "tutorial" | "interface" | "models" | "runtime" | "all";
interface Plan { profile_id: string; generation: string; data_root: string; profile: string; scopes: Scope[]; world_cards: number }
const API = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "/api";
const scopes: { id: Scope; zh: string; en: string; detail: [string, string] }[] = [
  { id: "workspace", zh: "工作区", en: "Workspace", detail: ["清空画布、会话及运行状态，重置教程；保留卡库和模型设置。", "Clear the world, conversations and runs; reset tutorial progress. Keep library and models."] },
  { id: "decks", zh: "卡组", en: "Decks", detail: ["恢复一个空的默认卡组，保留收藏和开包进度。", "Restore one empty deck. Keep collected cards and pack progress."] },
  { id: "packs", zh: "卡包与收藏", en: "Packs and collection", detail: ["恢复未开包状态，清除收藏及卡组中的卡牌引用。", "Close packs and clear collected cards and their deck references."] },
  { id: "tutorial", zh: "教程进度", en: "Tutorial progress", detail: ["恢复新用户状态，保留画布卡片；首次欢迎需空工作区。", "Reset progress, keeping placed cards. First-launch welcome requires an empty world."] },
  { id: "interface", zh: "界面与布局", en: "Interface and layout", detail: ["重置视角、面板、粘贴布局、语言及主题。", "Reset viewport, surfaces, sticking layout, language and theme."] },
  { id: "models", zh: "模型与凭据", en: "Models and credentials", detail: ["清除本开发档案的模型连接及其 API 密钥。", "Clear this development profile's model connections and API keys."] },
  { id: "runtime", zh: "Sandbox 运行环境", en: "Sandbox runtime", detail: ["重新准备共享 Python 环境及依赖；不删除外部工作文件夹。", "Rebuild shared Python and dependencies. External workspaces are preserved."] },
  { id: "all", zh: "完全初始化", en: "Complete reset", detail: ["重置全部上述状态、已保存军团和设置，包括模型密钥。", "Reset everything above, saved Legions and settings, including model credentials."] },
];

async function debugRequest<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`${API}/debug${path}`, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(typeof value.detail === "string" ? value.detail : `Debug request failed (${response.status})`);
  }
  return response.json();
}

export default function DevelopmentPanel() {
  const enabled = currentProfile()?.mode === "development";
  const chinese = useLocale(state => state.locale === "zh-CN");
  const tr = (zh: string, en: string) => chinese ? zh : en;
  const [open, setOpen] = useState(false);
  const [selection, setSelection] = useState<Scope[]>(["workspace"]);
  const [plan, setPlan] = useState<Plan>();
  const [busy, setBusy] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState("");
  const close = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "F3" && !event.repeat) {
        event.preventDefault(); event.stopImmediatePropagation();
        setOpen(value => !value);
      }
      if (event.key === "Escape" && open && !busy) {
        event.preventDefault(); event.stopImmediatePropagation(); setOpen(false);
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  }, [enabled, open, busy]);
  useEffect(() => {
    if (open) { previousFocus.current = document.activeElement as HTMLElement; close.current?.focus(); }
    else previousFocus.current?.focus();
  }, [open]);
  if (!enabled) return null;
  const choose = (value: Scope[]) => { setSelection(value); setPlan(undefined); setError(""); };
  const review = async () => {
    setBusy(true); setError("");
    try { await flushPreferences(); setPlan(await debugRequest<Plan>("/plan", { scopes: selection })); }
    catch (caught) { setError(String(caught instanceof Error ? caught.message : caught)); }
    finally { setBusy(false); }
  };
  const reset = async () => {
    if (!plan) return;
    setBusy(true); setError("");
    try {
      await flushPreferences();
      await debugRequest("/reset", { scopes: selection, profile_id: plan.profile_id, generation: plan.generation });
      setRestarting(true);
      // The profile monitor reloads every connected window after the generation changes.
    } catch (caught) { setError(String(caught instanceof Error ? caught.message : caught)); setBusy(false); setPlan(undefined); }
  };
  return <>
    <button className="development-badge" onClick={() => setOpen(value => !value)} title={tr("开发调试 · F3", "Development tools · F3")}><Bug size={13} /> DEV · F3</button>
    {open && <div className="development-backdrop" onPointerDown={event => { if (event.target === event.currentTarget && !busy) setOpen(false); }}>
      <section className="development-panel" role="dialog" aria-modal="true" aria-labelledby="development-title" onKeyDown={event => {
        if (event.key !== "Tab") return;
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)'));
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
        <header><div><small>OPEN AGENT WORLD · DEVELOPMENT</small><h2 id="development-title">{tr("开发调试", "Development tools")}</h2></div><button ref={close} disabled={busy} onClick={() => setOpen(false)} aria-label={tr("关闭调试面板", "Close development tools")}><X size={20} /></button></header>
        <p>{tr("选择需要初始化的内容。操作仅作用于当前开发档案，重置前会保留恢复备份。", "Choose what to initialize in this development profile. A recovery backup is kept before reset.")}</p>
        <div className="development-presets">
          <button disabled={busy} onClick={() => choose(["workspace", "decks", "packs", "interface"])}>{tr("测试首次启动", "Test first launch")}</button>
          <button disabled={busy} onClick={() => choose(["packs", "decks"])}>{tr("测试开包", "Test pack opening")}</button>
          <button disabled={busy} onClick={() => choose(["tutorial"])}>{tr("重置教程", "Reset tutorial")}</button>
        </div>
        <fieldset disabled={busy} className="development-scopes"><legend>{tr("初始化范围", "Reset scope")}</legend>{scopes.map(scope => <label key={scope.id} className={scope.id === "all" ? "development-complete" : ""}>
          <input type="checkbox" checked={selection.includes(scope.id)} disabled={scope.id !== "all" && selection.includes("all")} onChange={event => choose(scope.id === "all" ? event.target.checked ? ["all"] : [] : event.target.checked ? [...selection, scope.id] : selection.filter(value => value !== scope.id))} />
          <span><strong>{chinese ? scope.zh : scope.en}</strong><small>{scope.detail[chinese ? 0 : 1]}</small></span>
        </label>)}</fieldset>
        {plan && <div className="development-review"><strong>{tr("确认初始化", "Confirm reset")} · {plan.profile}</strong><code>{plan.data_root}</code>
          <p>{plan.scopes.map(id => { const scope = scopes.find(item => item.id === id)!; return chinese ? scope.zh : scope.en; }).join(" · ")}</p>
          {plan.scopes.includes("workspace") && <p>{tr(`将清空整个世界中的 ${plan.world_cards} 张卡片。`, `Clear all ${plan.world_cards} cards in the world.`)}</p>}
          <small>{tr("后端将停止任务、关闭数据库，执行重置后自动重启。外部工作文件夹保留。", "The backend will stop tasks, close the database, reset and restart. External workspaces are kept.")}</small>
        </div>}
        {error && <p role="alert" className="development-error">{error}</p>}
        {restarting && <p role="status"><LoaderCircle className="is-spinning" size={16} /> {tr("正在清理和重启，完成后页面将自动刷新。如未恢复，请检查启动日志。", "Cleaning up and restarting. This page reloads when ready. Check the launcher log if startup fails.")}</p>}
        <footer><button disabled={busy} onClick={() => { useWorldStore.getState().clearStressWorld(); useWorldStore.getState().generateStressWorld(1000); }}>{tr("生成 1000 张压力测试卡片", "Generate 1,000 stress cards")}</button><button disabled={busy} onClick={() => useWorldStore.getState().clearStressWorld()}>{tr("清除测试卡片", "Clear stress cards")}</button>
          <button className="development-primary" disabled={busy || !selection.length} onClick={() => void (plan ? reset() : review())}>{busy ? <LoaderCircle className="is-spinning" size={16} /> : <RotateCcw size={16} />}{plan ? tr("确认并重启", "Confirm and restart") : tr("预览重置范围", "Review reset")}</button></footer>
      </section>
    </div>}
  </>;
}
