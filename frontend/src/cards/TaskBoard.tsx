import { Check, Circle, GitBranch, ListTodo, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";

export interface BoardTask {
  id: string; title: string; description: string; status: "todo" | "doing" | "done" | "blocked"; depends_on: string[]; note: string;
}
interface BoardSnapshot { value: { tasks: BoardTask[] }; revision: number; summary: { total: number; done: number; ready_ids: string[] } }
const statuses = { todo: "To do", doing: "In progress", done: "Done", blocked: "Blocked" };
const snapshot = (value: unknown) => value as BoardSnapshot;

function useBoard(id: string) {
  const eventId = useWorldStore((state) => state.events.find((e) => e.payload.scope_kind === "node_document" && e.payload.owner_id === id)?.id);
  const socketState = useWorldStore((state) => state.socketState);
  const [board, setBoard] = useState<BoardSnapshot>();
  const [error, setError] = useState("");
  const accept = useCallback((next: BoardSnapshot) => setBoard((current) => !current || next.revision >= current.revision ? next : current), []);
  const reload = useCallback(async () => {
    try { accept(snapshot(await worldApi.getNodeDocument(id))); setError(""); }
    catch (e) { setError(apiErrorMessage(e)); }
  }, [id, accept]);
  useEffect(() => {
    let active = true;
    worldApi.getNodeDocument(id).then((value) => { if (active) accept(snapshot(value)); }).catch((e) => { if (active) setError(apiErrorMessage(e)); });
    return () => { active = false; };
  }, [id, eventId, socketState, accept]);
  return { board, accept, reload, error, setError };
}

export function TaskBoardPreview({ card }: { card: WorldCard }) {
  const { board, error } = useBoard(card.id);
  return <div className="node-preview-summary task-board-preview">
    <p>{board ? `${board.summary.done} of ${board.summary.total} tasks complete` : error || "Loading tasks..."}</p>
    <progress aria-label="Task completion" value={board?.summary.done ?? 0} max={board?.summary.total || 1} />
    <div className="node-preview-metadata"><span><ListTodo size={12} /> {board?.summary.ready_ids.length ?? 0} ready</span><span>Tasks & dependencies</span></div>
  </div>;
}

function DependencyGraph({ tasks, onSelect }: { tasks: BoardTask[]; onSelect: (task: BoardTask) => void }) {
  const arrowId = useId().replaceAll(":", "");
  const levels = new Map<string, number>();
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const depth = (id: string): number => {
    if (levels.has(id)) return levels.get(id)!;
    levels.set(id, 0);
    const dependencies = byId.get(id)?.depends_on ?? [];
    const result = dependencies.length ? 1 + Math.max(...dependencies.map(depth)) : 0;
    levels.set(id, result); return result;
  };
  const rows = new Map<number, number>();
  const positions = new Map(tasks.map((task) => {
    const column = depth(task.id); const row = rows.get(column) ?? 0; rows.set(column, row + 1);
    return [task.id, { x: 24 + column * 240, y: 24 + row * 90 }];
  }));
  return <div className="task-dependency-graph nowheel" aria-label="Task dependency graph"><svg role="img" aria-label="Prerequisites flow from left to right" width={Math.max(680, (Math.max(0, ...levels.values()) + 1) * 240)} height={Math.max(270, Math.max(0, ...rows.values()) * 90 + 36)}>
    <defs><marker id={arrowId} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><polygon points="0 0, 7 3.5, 0 7" fill="#8b9c94" /></marker></defs>
    {tasks.flatMap((task) => task.depends_on.map((dependency) => {
      const source = positions.get(dependency); const target = positions.get(task.id)!;
      return source ? <path markerEnd={`url(#${arrowId})`} key={`${dependency}-${task.id}`} d={`M${source.x + 192},${source.y + 29} C${source.x + 219},${source.y + 29} ${target.x - 27},${target.y + 29} ${target.x},${target.y + 29}`} /> : null;
    }))}
    {tasks.map((task) => { const point = positions.get(task.id)!; return <g key={task.id} transform={`translate(${point.x},${point.y})`} role="button" tabIndex={0} aria-label={`Edit task ${task.title}`} onClick={() => onSelect(task)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(task); } }}>
      <rect width={192} height={58} rx={12} className={`is-${task.status}`} /><text x={12} y={23}>{task.title.length > 24 ? task.title.slice(0, 23) + "..." : task.title}</text><text x={12} y={43} className="task-graph-status">{statuses[task.status]}</text><title>{task.title}</title>
    </g>; })}
  </svg></div>;
}

export function TaskBoardBody({ card, workspace = false }: { card: WorldCard; workspace?: boolean }) {
  const { board, accept, reload, error, setError } = useBoard(card.id);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"list" | "graph">("list");
  const [filter, setFilter] = useState<"all" | "ready" | "done">("all");
  const [quickTitle, setQuickTitle] = useState("");
  const [draft, setDraft] = useState<{ task: BoardTask; revision: number }>();
  const tasks = board?.value.tasks ?? [];
  const ready = new Set(board?.summary.ready_ids ?? []);
  const select = (task: BoardTask) => setDraft({ task: { ...task, depends_on: [...task.depends_on] }, revision: board!.revision });
  const mutate = async (action: string, args: Record<string, unknown>, revision = board?.revision): Promise<boolean> => {
    if (revision === undefined) return false;
    setBusy(true); setError("");
    try { accept(snapshot(await worldApi.nodeDocumentAction(card.id, action, args, revision))); return true; }
    catch (e) { setError(apiErrorMessage(e)); return false; }
    finally { setBusy(false); }
  };
  const add = async () => {
    if (!quickTitle.trim()) return;
    const task: BoardTask = { id: `task_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`, title: quickTitle.trim(), description: "", status: "todo", depends_on: [], note: "" };
    if (await mutate("upsert", { tasks: [task] })) setQuickTitle("");
  };
  const patch = (change: Partial<BoardTask>) => setDraft((current) => current ? { ...current, task: { ...current.task, ...change } } : current);
  return <div className={`task-board nodrag nopan nowheel ${workspace ? "is-workspace" : ""}`}>
    <header className="task-board-heading"><div><span className="task-board-eyebrow">SHARED WORK BOARD</span><h3>{board ? `${board.summary.done} / ${board.summary.total} complete` : "Loading tasks..."}</h3></div>
      <button className="secondary-button" title="Reload the board and discard the open task draft" aria-label="Reload task board" disabled={busy} onClick={() => { setDraft(undefined); void reload(); }}><RefreshCw size={14} /></button></header>
    <progress aria-label="Task completion" value={board?.summary.done ?? 0} max={board?.summary.total || 1} />
    <p className="task-board-help">Connect an Agent to read tasks, update progress, or manage the plan.</p>
    <div className="task-board-toolbar"><div className="task-view-toggle" role="group" aria-label="Task view"><button aria-pressed={view === "list"} onClick={() => setView("list")}><ListTodo size={14} /> List</button><button aria-pressed={view === "graph"} onClick={() => setView("graph")}><GitBranch size={14} /> Dependencies</button></div>
      <select aria-label="Filter tasks" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} disabled={view === "graph"}><option value="all">All tasks</option><option value="ready">Ready to start</option><option value="done">Completed</option></select></div>
    {error && <p className="task-board-error" role="alert">{error}</p>}
    <div className="task-board-layout"><div className="task-board-main">
      <form className="task-quick-add" onSubmit={(e) => { e.preventDefault(); void add(); }}><input aria-label="New task title" placeholder="What needs to be done?" value={quickTitle} maxLength={200} onChange={(e) => setQuickTitle(e.target.value)} disabled={busy || !board} /><button className="primary-button" aria-label="Add task" disabled={busy || !board || !quickTitle.trim()}><Plus size={16} /></button></form>
      {tasks.length === 0 && board ? <div className="task-board-empty"><ListTodo size={28} /><strong>A clear place to start.</strong><p>Add your first task. Dependencies are optional.</p></div>
        : view === "graph" ? <DependencyGraph tasks={tasks} onSelect={select} /> : <div className="task-list" aria-label="Tasks">{tasks.filter((task) => filter === "all" || (filter === "ready" ? ready.has(task.id) : task.status === "done")).map((task) => {
          const waiting = task.status === "todo" && !ready.has(task.id);
          return <article className={`task-row is-${task.status} ${draft?.task.id === task.id ? "is-selected" : ""}`} key={task.id}>
            <button className="task-check" disabled={busy || (task.status !== "done" && (waiting || task.status === "blocked"))} aria-label={`${task.status === "done" ? "Reopen" : "Complete"} ${task.title}`} title={waiting ? "Complete prerequisites first" : task.status === "blocked" ? "Resolve the blocker in task details first" : undefined}
              onClick={() => void mutate("progress", { task_id: task.id, status: task.status === "done" ? "todo" : "done", note: task.note })}>{task.status === "done" ? <Check size={16} /> : <Circle size={16} />}</button>
            <button className="task-row-content" onClick={() => select(task)} aria-label={`Edit task ${task.title}`}><strong>{task.title}</strong><span>{waiting ? "Waiting for prerequisites" : ready.has(task.id) ? "Ready to start" : statuses[task.status]}{task.depends_on.length > 0 && ` / ${task.depends_on.length} dependencies`}</span></button>
          </article>;
        })}</div>}
    {tasks.length > 0 && view === "list" && filter !== "all" && !tasks.some((task) => filter === "ready" ? ready.has(task.id) : task.status === "done") && <p className="task-board-help">{filter === "ready" ? "No tasks are ready. Check prerequisites and blockers." : "No completed tasks yet."}</p>}
    </div>
    {draft && <form className="task-editor" aria-label="Task details" onSubmit={async (e) => { e.preventDefault(); if (await mutate("upsert", { tasks: [draft.task] }, draft.revision)) setDraft(undefined); }}>
      <header><strong>Task details</strong><button type="button" aria-label="Close task details" onClick={() => setDraft(undefined)}><X size={16} /></button></header>
      <fieldset disabled={busy}><label>Title<input aria-label="Task title" maxLength={200} required value={draft.task.title} onChange={(e) => patch({ title: e.target.value })} /></label>
      <label>Description<textarea aria-label="Task description" rows={3} maxLength={4000} placeholder="Expected outcome or instructions" value={draft.task.description} onChange={(e) => patch({ description: e.target.value })} /></label>
      <label>Status<select aria-label="Task status" value={draft.task.status} onChange={(e) => patch({ status: e.target.value as BoardTask["status"] })}>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Progress note<textarea aria-label="Progress note" rows={2} maxLength={4000} placeholder="Result, blocker, or next step" value={draft.task.note} onChange={(e) => patch({ note: e.target.value })} /></label>
      <div className="task-dependencies"><strong>Depends on</strong><p>These tasks must finish first.</p>{tasks.filter((task) => task.id !== draft.task.id).map((task) => <label key={task.id}><input type="checkbox" aria-label={`Depends on ${task.title}`} checked={draft.task.depends_on.includes(task.id)} onChange={(e) => patch({ depends_on: e.target.checked ? [...draft.task.depends_on, task.id] : draft.task.depends_on.filter((id) => id !== task.id) })} />{task.title}</label>)}{tasks.length < 2 && <p>Add another task to set a dependency.</p>}</div>
      <div className="task-editor-actions"><button type="button" className="secondary-button" aria-label="Delete task" title="Delete this task" onClick={async () => { if (await mutate("remove", { task_id: draft.task.id }, draft.revision)) setDraft(undefined); }}><Trash2 size={14} /></button><button className="primary-button">Save task</button></div></fieldset>
      <small>Task ID: {draft.task.id}</small>
    </form>}
    </div><p className="task-board-help task-board-footnote">Dependencies control readiness. Tasks run when you or an Agent act on them.</p>
  </div>;
}
