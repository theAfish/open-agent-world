// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CardFinishLayer, type CardFinishQuality } from "./CardFinishLayer";
import type { CardFinish } from "./cardFinish";
import { useCardFinish } from "./useCardFinish";

let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;
let reduced: boolean;
let paints: number;
const schedule = vi.fn((callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; });
const cancel = vi.fn((frame: number) => { frames.delete(frame); });

beforeEach(() => {
  frames = new Map(); nextFrame = 0; reduced = false; paints = 0;
  schedule.mockClear(); cancel.mockClear();
  vi.stubGlobal("requestAnimationFrame", schedule);
  vi.stubGlobal("cancelAnimationFrame", cancel);
  vi.stubGlobal("PointerEvent", MouseEvent);
  vi.stubGlobal("matchMedia", () => ({ get matches() { return reduced; } }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Surface({ finish = "foil", quality = "standard", tilt = false, onClick }: {
  finish?: CardFinish;
  quality?: CardFinishQuality;
  tilt?: boolean;
  onClick?: () => void;
}) {
  paints += 1;
  const lighting = useCardFinish(finish, quality, tilt);
  return <button className="card-finish-surface" {...lighting} onClick={onClick}>
    Printed face
    <CardFinishLayer finish={finish} quality={quality} />
  </button>;
}

function measure(element: HTMLElement) {
  return vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    left: 10, top: 20, width: 200, height: 300, right: 210, bottom: 320, x: 10, y: 20, toJSON() {},
  });
}

function flush() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach(callback => callback(16));
  });
}

describe("CardFinishLayer", () => {
  it("adds no overlay for legacy or normal cards and preserves the assigned plate on rerender", () => {
    const { container, rerender } = render(<CardFinishLayer />);
    expect(container.childElementCount).toBe(0);
    rerender(<CardFinishLayer finish="normal" />);
    expect(container.childElementCount).toBe(0);
    rerender(<CardFinishLayer finish="starlight" reveal />);
    const plate = container.firstElementChild;
    expect(plate?.getAttribute("data-finish")).toBe("starlight");
    expect(plate?.getAttribute("aria-hidden")).toBe("true");
    expect(plate?.getAttribute("data-reveal")).toBe("true");
    const texture = plate?.getAttribute("style");
    rerender(<CardFinishLayer finish="starlight" quality="showcase" />);
    expect(container.firstElementChild).toBe(plate);
    expect(plate?.getAttribute("data-finish")).toBe("starlight");
    expect(plate?.getAttribute("style")).toBe(texture);
    expect(schedule).not.toHaveBeenCalled();
  });

  it("coalesces pointer events, measures once, keeps the light fixed, and leaves host transforms alone", () => {
    const clicked = vi.fn();
    const { getByRole } = render(<Surface onClick={clicked} />);
    const node = getByRole("button");
    node.style.transform = "rotate(7deg)";
    const bounds = measure(node);
    fireEvent.pointerEnter(node, { clientX: 110, clientY: 170 });
    fireEvent.pointerMove(node, { clientX: 200, clientY: 30 });
    fireEvent.pointerMove(node, { clientX: 410, clientY: -200 });
    expect(schedule).toHaveBeenCalledTimes(1);
    flush();
    expect(node.style.getPropertyValue("--pointer-x")).toBe("1.000");
    expect(node.style.getPropertyValue("--pointer-y")).toBe("-1.000");
    expect(node.style.getPropertyValue("--finish-light-x")).toBe("32%");
    expect(node.style.getPropertyValue("--finish-light-y")).toBe("24%");
    expect(node.style.getPropertyValue("--card-angle-x")).toBe("");
    expect(node.style.transform).toBe("rotate(7deg)");
    expect(bounds).toHaveBeenCalledTimes(1);
    expect(paints).toBe(1);
    expect(frames.size).toBe(0);
    fireEvent.click(node);
    expect(clicked).toHaveBeenCalledOnce();
    fireEvent.pointerLeave(node);
    expect(node.hasAttribute("data-finish-active")).toBe(false);
    expect(node.style.getPropertyValue("--pointer-x")).toBe("");
    expect(node.style.transform).toBe("rotate(7deg)");
  });

  it("cancels a pending update on leave, finish change, and unmount", () => {
    const { getByRole, rerender, unmount } = render(<Surface />);
    const node = getByRole("button");
    measure(node);
    fireEvent.pointerEnter(node, { clientX: 40, clientY: 60 });
    fireEvent.pointerLeave(node);
    expect(frames.size).toBe(0);
    expect(cancel).toHaveBeenCalledTimes(1);
    fireEvent.pointerEnter(node, { clientX: 40, clientY: 60 });
    rerender(<Surface finish="rainbow" />);
    expect(frames.size).toBe(0);
    fireEvent.pointerEnter(node, { clientX: 40, clientY: 60 });
    flush();
    fireEvent.pointerMove(node, { clientX: 90, clientY: 70 });
    unmount();
    expect(frames.size).toBe(0);
    expect(cancel).toHaveBeenCalledTimes(3);
    expect(node.style.getPropertyValue("--finish-light-x")).toBe("");
  });

  it.each(["normal", "laser"] as const)("tilts opted-in %s thumbnails only during interaction and resets on scroll or drag", finish => {
    const { getByRole } = render(<Surface finish={finish} quality="thumbnail" tilt />);
    const node = getByRole("button");
    measure(node);
    expect(schedule).not.toHaveBeenCalled();
    fireEvent.pointerEnter(node, { clientX: 190, clientY: 50 });
    flush();
    expect(node.style.getPropertyValue("--card-angle-x")).toBe("4.00deg");
    expect(node.style.getPropertyValue("--card-angle-y")).toBe("4.00deg");
    expect(node.getAttribute("data-card-tilting")).toBe("true");
    expect(paints).toBe(1);
    expect(frames.size).toBe(0);
    fireEvent.scroll(window);
    expect(node.hasAttribute("data-card-tilting")).toBe(false);
    expect(node.style.getPropertyValue("--card-angle-x")).toBe("");
    fireEvent.pointerEnter(node, { clientX: 190, clientY: 50 });
    fireEvent.pointerMove(node, { clientX: 190, clientY: 50, buttons: 1 });
    expect(frames.size).toBe(0);
    expect(node.hasAttribute("data-card-tilting")).toBe(false);
  });

  it("keeps touch and reduced-motion collectible faces still", () => {
    const { getByRole } = render(<Surface finish="normal" tilt />);
    const node = getByRole("button");
    const bounds = measure(node);
    const touch = new MouseEvent("pointerover", { bubbles: true, clientX: 80, clientY: 100 });
    Object.defineProperty(touch, "pointerType", { value: "touch" });
    fireEvent(node, touch);
    expect(schedule).not.toHaveBeenCalled();
    reduced = true;
    fireEvent.pointerEnter(node, { clientX: 80, clientY: 100 });
    expect(schedule).not.toHaveBeenCalled();
    expect(bounds).not.toHaveBeenCalled();
  });

  it("keeps reduced motion static, including a preference change during interaction", () => {
    reduced = true;
    const { getByRole } = render(<Surface finish="rainbow" />);
    const node = getByRole("button");
    const bounds = measure(node);
    fireEvent.pointerEnter(node, { clientX: 80, clientY: 100 });
    fireEvent.pointerMove(node, { clientX: 150, clientY: 190 });
    expect(schedule).not.toHaveBeenCalled();
    expect(bounds).not.toHaveBeenCalled();
    reduced = false;
    fireEvent.pointerEnter(node, { clientX: 80, clientY: 100 });
    flush();
    reduced = true;
    fireEvent.pointerMove(node, { clientX: 150, clientY: 190 });
    expect(node.hasAttribute("data-finish-active")).toBe(false);
    expect(frames.size).toBe(0);
  });

  it("does not schedule work for hundreds of passive thumbnails or normal cards", () => {
    const { getAllByRole, container } = render(<>
      {Array.from({ length: 200 }, (_, index) => <Surface key={index} quality="thumbnail" finish="laser" />)}
      <Surface finish="normal" />
    </>);
    getAllByRole("button").forEach(node => {
      fireEvent.pointerEnter(node, { clientX: 40, clientY: 60 });
      fireEvent.pointerMove(node, { clientX: 80, clientY: 90 });
    });
    expect(container.querySelectorAll(".card-finish-layer")).toHaveLength(200);
    expect(container.querySelectorAll(".card-finish-grain")).toHaveLength(0);
    expect(schedule).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(paints).toBe(201);
  });
});
