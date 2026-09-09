import { type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useStore } from '@xyflow/react';

/** React Flow's first-descendant lookup can select a nested graph's portal. */
export function ownedFlowPortal(root: HTMLElement | null, className: string) {
  if (!root) return null;
  return Array.from(root.querySelectorAll<HTMLElement>(`.${className}`))
    .find(element => element.closest('.react-flow') === root) ?? null;
}

export function ViewportPortal({ children }: { children: ReactNode }) {
  const target = useStore(state => ownedFlowPortal(state.domNode, 'react-flow__viewport-portal'));
  return target ? createPortal(children, target) : null;
}

export function EdgeLabelRenderer({ children }: { children: ReactNode }) {
  const target = useStore(state => ownedFlowPortal(state.domNode, 'react-flow__edgelabel-renderer'));
  return target ? createPortal(children, target) : null;
}
