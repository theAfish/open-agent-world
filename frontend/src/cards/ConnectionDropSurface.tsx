import { Handle, Position } from "@xyflow/react";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";

/** Receive a connection through the native handle validation and preview path.
 * Keep this mounted for handle measurement, but never intercept normal card input.
 */
export function ConnectionDropSurface({ nodeId }: { nodeId: string }) {
  const active = useNodeSurfaceStore(state => Boolean(state.connectingNodeId && state.connectingNodeId !== nodeId));
  return <Handle type="target" id="surface-drop" position={Position.Top}
    className="connection-drop-surface" data-active={active || undefined}
    isConnectableStart={false} isConnectableEnd={active} aria-hidden="true" />;
}
