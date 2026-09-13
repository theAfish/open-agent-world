import { ReactFlowProvider } from "@xyflow/react";
import { useEffect } from "react";
import { WorldCanvas } from "./canvas/WorldCanvas";
import { ConnectionDialog } from "./edges/ConnectionDialog";
import { ComponentPalette } from "./palette/ComponentPalette";
import { LegionSelection } from "./legions/LegionSelection";
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
        <TopBar />
        <BackendUnavailableNotice />
        <LegionSelection />
        <ComponentPalette />
        <Onboarding />
        <ActivityPanel />
        <ConnectionDialog />
        <ToastStack />
        <RuntimeConnection />
        <SettingsPanel />
        <CardLibrary />
      </main>
    </ReactFlowProvider>
  );
}
