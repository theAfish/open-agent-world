import ReactDOM from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./theme.css";
import "./startup.css";
import { initializeProfile } from "./state/profileStorage";

async function start() {
  const root = document.getElementById("root")!;
  root.classList.add("application-startup");
  root.textContent = "Open Agent World · 正在连接工作区 / Connecting to your workspace…";
  try {
    const apiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? '/api';
    const response = await fetch(`${apiBase}/deployment`, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('Deployment mode unavailable');
    const mode = await response.json();
    if (mode.mode === 'runtime') {
      const { RuntimeApp } = await import('./deployment/RuntimeApp');
      root.classList.remove('application-startup');
      ReactDOM.createRoot(root).render(<RuntimeApp name={mode.name} />);
      return;
    }
    await initializeProfile();
    const { App } = await import("./App");
    root.classList.remove("application-startup");
    ReactDOM.createRoot(root).render(<App />);
  } catch {
    root.textContent = "工作区暂未就绪，请检查启动日志。 / The workspace is not ready. Check the launcher log. ";
    const retry = document.createElement("button");
    retry.textContent = "重试 / Retry";
    retry.onclick = () => { void start(); };
    root.append(retry);
  }
}
void start();
