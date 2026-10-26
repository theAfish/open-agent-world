import ReactDOM from "react-dom/client";
import type { ReactNode } from 'react';
import "@xyflow/react/dist/style.css";
import "./theme.css";
import "./startup.css";
import "./components/controls.css";
import './auth/hosted.css';
import { initializeProfile } from "./state/profileStorage";
import { fetchWithRetry } from './api/fetchWithRetry';
import { initializeHostedSession, isHosted } from './auth/hostedSession';

let view: ReturnType<typeof ReactDOM.createRoot> | undefined;
window.addEventListener('oaw-hosted-session-ended', () => { view?.unmount(); view = undefined; });

async function start() {
  const root = document.getElementById("root")!;
  root.classList.add('application-startup');
  root.textContent = 'Open Agent World · 正在连接工作区 / Connecting to your workspace…';
  try {
    const session = await initializeHostedSession();
    if (session?.authenticated && (location.pathname === '/admin' || (session.user?.role === 'admin' && !session.instance))) {
      const { HostedAdmin } = await import('./auth/HostedAdmin');
      root.classList.remove('application-startup');
      view = ReactDOM.createRoot(root);
      view.render(<HostedAdmin session={session} />);
      return;
    }
    if (session && (!session.authenticated || !session.instance)) {
      const { HostedSignIn } = await import('./auth/HostedSignIn');
      root.classList.remove('application-startup');
      view = ReactDOM.createRoot(root);
      view.render(<HostedSignIn session={session} />);
      return;
    }
    const render = (element: ReactNode) => {
      root.classList.remove('application-startup');
      view = ReactDOM.createRoot(root);
      view.render(element);
    };
    if (import.meta.env.DEV && !isHosted() && new URLSearchParams(location.search).has('network-map')) {
      const { NetworkMapPreview } = await import('./debug/NetworkMapPreview');
      render(<NetworkMapPreview />);
      return;
    }
    if (import.meta.env.DEV && !isHosted() && new URLSearchParams(location.search).has('card-studio')) {
      const { CardProductionPreview } = await import('./debug/CardProductionPreview');
      render(<CardProductionPreview />);
      return;
    }
    if (import.meta.env.DEV && !isHosted() && new URLSearchParams(location.search).has('card-materials')) {
      const { CardMaterialLab } = await import('./debug/CardMaterialLab');
      render(<CardMaterialLab />);
      return;
    }
    if (import.meta.env.DEV && !isHosted() && new URLSearchParams(location.search).has('pack-design')) {
      const { PackDesignPreview } = await import('./debug/PackDesignPreview');
      render(<PackDesignPreview />);
      return;
    }
    if (import.meta.env.DEV && !isHosted() && new URLSearchParams(location.search).has('card-design')) {
      const { CardDesignPreview } = await import('./debug/CardDesignPreview');
      render(<CardDesignPreview />);
      return;
    }
    if (import.meta.env.DEV && !isHosted() && new URLSearchParams(location.search).has('card-finishes')) {
      const { FinishPreview } = await import('./debug/FinishPreview');
      const { SelectMenuLayer } = await import('./components/SelectMenuLayer');
      render(<><SelectMenuLayer /><FinishPreview /></>);
      return;
    }
    // Import persisted application stores only after establishing the account.
    const apiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? '/api';
    if (isHosted() && new URL(apiBase, window.location.href).origin !== window.location.origin) {
      throw new Error('Hosted workspaces require the same-origin API.');
    }
    // A hosted worker may need to load its Packs after socket activation.
    const response = await fetchWithRetry(`${apiBase}/deployment`, { cache: 'no-store', signal: AbortSignal.timeout(isHosted() ? 60_000 : 5000) });
    if (!response.ok) throw new Error('Deployment mode unavailable');
    const mode = await response.json();
    if (mode.mode !== 'runtime') await initializeProfile();
    const { SelectMenuLayer } = await import('./components/SelectMenuLayer');
    if (mode.mode === 'runtime') {
      const { RuntimeApp } = await import('./deployment/RuntimeApp');
      render(<><SelectMenuLayer /><RuntimeApp name={mode.name} /></>);
      return;
    }
    const { App } = await import("./App");
    render(<><SelectMenuLayer /><App /></>);
  } catch {
    root.textContent = "工作区暂未就绪，请检查启动日志。 / The workspace is not ready. Check the launcher log. ";
    const retry = document.createElement("button");
    retry.textContent = "重试 / Retry";
    retry.onclick = () => { void start(); };
    root.append(retry);
  }
}
void start();
