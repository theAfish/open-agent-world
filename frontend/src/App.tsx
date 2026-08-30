import { ReactFlowProvider } from "@xyflow/react";
import { useEffect } from "react";
import { WorldCanvas } from "./canvas/WorldCanvas";
import { ConnectionDialog } from "./edges/ConnectionDialog";
import { ComponentPalette } from "./palette/ComponentPalette";
import { ActivityPanel } from "./shell/ActivityPanel";
import { EmptyWorld } from "./shell/EmptyWorld";
import { RuntimeConnection } from "./shell/RuntimeConnection";
import { ToastStack } from "./shell/ToastStack";
import { TopBar } from "./shell/TopBar";
import { useWorldStore } from "./state/worldStore";

export function App() {
  const initialize = useWorldStore((state) => state.initialize);
  const theme = useWorldStore((state) => state.theme);

  useEffect(() => {
    void initialize();
  }, [initialize]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <ReactFlowProvider>
      <main className="world-shell">
        <WorldCanvas />
        <TopBar />
        <ComponentPalette />
        <EmptyWorld />
        <ActivityPanel />
        <ConnectionDialog />
        <ToastStack />
        <RuntimeConnection />
      </main>
    </ReactFlowProvider>
  );
}
