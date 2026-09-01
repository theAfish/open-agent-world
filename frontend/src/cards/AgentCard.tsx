import { CircleStop, Play, Radio } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getRelationshipOption } from "../state/relationships";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { InstrumentOutput } from "./CardUtilities";

export function AgentCardBody({ card }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const updateCard = useWorldStore((state) => state.updateCard);
  const runAgent = useWorldStore((state) => state.runAgent);
  const stopAgent = useWorldStore((state) => state.stopAgent);
  const modelSettings = useWorldStore((state) => state.modelSettings);
  const [instruction, setInstruction] = useState(String(card.config.system_instruction ?? ""));
  const [model, setModel] = useState(String(card.config.model ?? "gemini-3.7-flash"));
  const prompt = useNodeSurfaceStore((state) => state.drafts[card.id] ?? String(card.config.prompt ?? ""));
  const setDraft = useNodeSurfaceStore((state) => state.setDraft);

  useEffect(() => setInstruction(String(card.config.system_instruction ?? "")), [card.config.system_instruction]);
  useEffect(() => setModel(String(card.config.model ?? "gemini-3.7-flash")), [card.config.model]);

  const capabilities = useMemo(
    () => edges
      .filter((edge) =>
        edge.source === card.id
        || (edge.target === card.id && edge.direction === "bidirectional"),
      )
      .map((edge) => ({
        edge,
        target: cards.find((item) => item.id === (
          edge.source === card.id ? edge.target : edge.source
        )),
      })),
    [card.id, cards, edges],
  );
  const output = Array.isArray(card.config.output)
    ? card.config.output.map(String)
    : [];
  const modelOptions = [...new Set([model, ...modelSettings.models].filter(Boolean))];

  return (
    <div className="expanded-stack nodrag nopan">
      <div className="field-row">
        <label>
          <span>Model</span>
          <select
            value={model}
            onChange={(event) => {
              const value = event.target.value;
              setModel(value);
              if (value !== card.config.model) {
                void updateCard(card.id, { config: { model: value } });
              }
            }}
          >
            {modelOptions.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        </label>
        <div className="live-readout">
          <Radio size={13} aria-hidden="true" />
          <span title="Google ADK selects the model adapter automatically.">ADK · {card.status}</span>
        </div>
      </div>

      <label className="field-label">
        <span>System instruction</span>
        <textarea
          value={instruction}
          rows={3}
          onChange={(event) => setInstruction(event.target.value)}
          onBlur={() => {
            if (instruction !== card.config.system_instruction) {
              void updateCard(card.id, { config: { system_instruction: instruction } });
            }
          }}
        />
      </label>

      <section className="card-section">
        <div className="section-heading">
          <span>Effective capabilities</span>
          <small>backend-derived</small>
        </div>
        <div className="capability-chips">
          {capabilities.length > 0 ? capabilities.map(({ edge, target }) => (
            <span key={edge.id} title={getRelationshipOption(edge.relationship).description}>
              {target?.name ?? edge.target} · {getRelationshipOption(edge.relationship).shortLabel}
            </span>
          )) : <em>Connect a resource or sandbox to grant a scoped tool.</em>}
        </div>
      </section>

      <label className="field-label prompt-field">
        <span>Prompt</span>
        <textarea
          value={prompt}
          rows={2}
          placeholder="Ask Atlas to work with its connected objects…"
          onChange={(event) => setDraft(card.id, event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && prompt.trim()) {
              void runAgent(card.id, prompt.trim());
            }
          }}
        />
      </label>

      <div className="action-row">
        <button
          type="button"
          className="primary-button"
          onClick={() => void runAgent(card.id, prompt.trim())}
          disabled={!prompt.trim() || card.status === "running"}
        >
          <Play size={14} fill="currentColor" /> Run agent
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={() => void stopAgent(card.id)}
          disabled={card.status !== "running" && card.status !== "waiting"}
        >
          <CircleStop size={14} /> Stop
        </button>
      </div>

      <section className="card-section output-section">
        <div className="section-heading"><span>Runtime activity</span><small>operational log</small></div>
        <InstrumentOutput lines={output} empty="Run output and tool activity will appear here." />
      </section>
    </div>
  );
}
