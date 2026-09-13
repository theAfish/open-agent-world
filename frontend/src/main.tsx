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
