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
import { RuntimeConnection } from "./shell/RuntimeConnection";
import { SettingsPanel } from "./shell/SettingsPanel";
import { ToastStack } from "./shell/ToastStack";
import { TopBar } from "./shell/TopBar";
import { CardLibrary } from "./shell/CardLibrary";
import { useWorldStore } from "./state/worldStore";
import { useLocale } from "./i18n";

const DevelopmentPanel = import.meta.env.DEV ? lazy(() => import("./debug/DevelopmentPanel")) : null;

export function App() {
  const locale = useLocale(state => state.locale);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
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
        <VisualObserver />
        <TopBar />
        <BackendUnavailableNotice />
        <LegionSelection />
        <LegionWorkspace />
        <ComponentPalette />
        <LegionHoverPanel />
        <Onboarding />
        <ActivityPanel />
        <ConnectionDialog />
        <ToastStack />
        <RuntimeConnection />
        <SettingsPanel />
        <CardLibrary />
        {DevelopmentPanel && <Suspense fallback={null}><DevelopmentPanel /></Suspense>}
      </main>
    </ReactFlowProvider>
  );
}
