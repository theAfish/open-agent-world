import { useEffect, useRef } from "react";
import { useReactFlow } from "@xyflow/react";
import { ViewportPortal } from "../canvas/FlowPortal";
import { flightPosition, useGenerationStore, type NodeGeneration } from "./generation";
import "./effects.css";

/** A detached, inert visual copy moves; React Flow nodes keep authoritative positions. */
function GenerationFlight({ generation }: { generation: NodeGeneration }) {
  const ref = useRef<HTMLDivElement>(null);
  const { getInternalNode } = useReactFlow();
  useEffect(() => {
    const store = useGenerationStore.getState();
    let frame = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let clone: HTMLElement | undefined;
    let source: HTMLElement | null;
    let target: HTMLElement | null;
    const settle = () => {
      clone?.remove();
      store.setPhase(generation.id, "settling");
      timer = setTimeout(() => store.remove(generation.id), 220);
    };
    const begin = () => {
      const portal = ref.current;
      if (!portal) return;
      source = document.querySelector<HTMLElement>(`[data-card-id="${CSS.escape(generation.sourceId)}"]`);
      target = document.querySelector<HTMLElement>(`[data-card-id="${CSS.escape(generation.targetId)}"]`);
      const sourceNode = getInternalNode(generation.sourceId);
      const targetNode = getInternalNode(generation.targetId);
      if (!target || !targetNode?.measured.width) {
        frame = requestAnimationFrame(begin);
        return;
      }
      const box = source?.getBoundingClientRect();
      const endBox = target.getBoundingClientRect();
      const inView = (rect: DOMRect) => rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
      if (!source || !sourceNode || !box || !inView(box) || !inView(endBox)
          || document.hidden || matchMedia("(prefers-reduced-motion: reduce)").matches) {
        settle();
        return;
      }
      clone = source.cloneNode(true) as HTMLElement;
      clone.removeAttribute("style");
      clone.querySelectorAll(".activity-glow, .semantic-handle, .equipment-toggle, button, input, textarea, select").forEach((item) => item.remove());
      for (const element of [clone, ...clone.querySelectorAll<HTMLElement>("*")]) {
        for (const attribute of [...element.attributes]) {
          if (attribute.name === "id" || attribute.name.startsWith("data-") || attribute.name.startsWith("aria-")) element.removeAttribute(attribute.name);
        }
      }
      clone.classList.remove("is-selected", "is-running", "is-error");
      clone.classList.add("generation-copy");
      clone.style.setProperty("--card-kind", getComputedStyle(source).getPropertyValue("--card-kind"));
      clone.inert = true;
      clone.style.width = `${sourceNode.measured.width}px`;
      clone.style.height = `${sourceNode.measured.height}px`;
      clone.style.minHeight = "0";
      portal.append(clone);
      store.setPhase(generation.id, "flying");
      const from = { ...sourceNode.internals.positionAbsolute };
      const width = sourceNode.measured.width ?? 96;
      const height = sourceNode.measured.height ?? 96;
      const began = performance.now();
      const tick = (now: number) => {
        const destination = getInternalNode(generation.targetId);
        if (!destination || !target?.isConnected || document.hidden || !clone) { settle(); return; }
        const progress = Math.min(1, Math.max(0, (now - began - 120) / 650));
        const point = flightPosition(from, destination.internals.positionAbsolute, progress);
        const eased = 1 - (1 - progress) ** 3;
        const sx = 1 + (((destination.measured.width ?? width) / width) - 1) * eased;
        const sy = 1 + (((destination.measured.height ?? height) / height) - 1) * eased;
        clone.style.transform = `translate(${point.x}px, ${point.y}px) scale(${sx}, ${sy})`;
        clone.style.opacity = String(0.9 - Math.max(0, progress - 0.85) * 2);
        if (progress < 1) frame = requestAnimationFrame(tick);
        else settle();
      };
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(begin);
    return () => {
      cancelAnimationFrame(frame); clearTimeout(timer); clone?.remove();
    };
  }, [generation.id, generation.sourceId, generation.targetId, getInternalNode]);
  return <div ref={ref} className="generation-flight" data-generation-id={generation.id} aria-hidden="true" />;
}

export function GenerationLayer() {
  const items = useGenerationStore((state) => state.items);
  return <ViewportPortal>{items.map((item) => <GenerationFlight key={item.id} generation={item} />)}</ViewportPortal>;
}
