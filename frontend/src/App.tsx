import { ReactFlowProvider } from "@xyflow/react";
import { lazy, Suspense, useEffect } from "react";
import { WorldCanvas } from "./canvas/WorldCanvas";
import { VisualObserver } from "./canvas/VisualObserver";
import { ConnectionDialog } from "./edges/ConnectionDialog";
import { ComponentPalette } from "./palette/ComponentPalette";
import { LegionSelection } from "./legions/LegionSelection";
import { LegionHoverPanel } from "./legions/LegionHoverPanel";
import { LegionWorkspace } from './legions/LegionWorkspace';
import { ActivityPanel } from "./shell/ActivityPanel";
import { BackendUnavailableNotice } from "./shell/BackendUnavailableNotice";
import { Onboarding } from "./onboarding/Onboarding";
import { ProgressiveTutorials } from './tutorials/ProgressiveTutorials';
import { RuntimeConnection } from "./shell/RuntimeConnection";
import { SettingsPanel } from "./shell/SettingsPanel";
import { ToastStack } from "./shell/ToastStack";
import { TopBar } from "./shell/TopBar";
import { CardLibrary } from "./shell/CardLibrary";
import { useWorldStore } from "./state/worldStore";
import './shell/motion.css';
import { isHosted } from './auth/hostedSession';
import { useApplicationAppearance } from './shell/useAppearance';

const DevelopmentPanel = import.meta.env.DEV ? lazy(() => import("./debug/DevelopmentPanel")) : null;

export function App() {
  useApplicationAppearance();
  const initialize = useWorldStore((state) => state.initialize);

  useEffect(() => {
    void initialize();
  }, [initialize]);

  return (
    <ReactFlowProvider>
      <main className="world-shell">
        <WorldCanvas />
        <VisualObserver />
        <TopBar />
        <BackendUnavailableNotice />
        <LegionSelection />
        <LegionWorkspace />
        <ComponentPalette />
        <LegionHoverPanel />
        <Onboarding />
        <ProgressiveTutorials />
        <ActivityPanel />
        <ConnectionDialog />
        <ToastStack />
        <RuntimeConnection />
        <SettingsPanel />
        <CardLibrary />
        {DevelopmentPanel && !isHosted() && <Suspense fallback={null}><DevelopmentPanel /></Suspense>}
      </main>
    </ReactFlowProvider>
  );
}
