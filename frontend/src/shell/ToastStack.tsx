import { AlertTriangle, Check, Info, X } from "lucide-react";
import { useEffect } from "react";
import { useWorldStore } from "../state/worldStore";
import type { ToastMessage } from "../types/world";

function Toast({ toast }: { toast: ToastMessage }) {
  const dismiss = useWorldStore((state) => state.dismissToast);
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(toast.id), toast.tone === "error" ? 7000 : 4500);
    return () => window.clearTimeout(timer);
  }, [dismiss, toast.id, toast.tone]);
  const Icon = toast.tone === "success" ? Check : toast.tone === "error" ? AlertTriangle : Info;
  return (
    <div className={`toast toast--${toast.tone}`} role={toast.tone === "error" ? "alert" : "status"}>
      <span className="toast-icon"><Icon size={15} /></span>
      <div><strong>{toast.title}</strong>{toast.detail ? <p>{toast.detail}</p> : null}</div>
      <button type="button" onClick={() => dismiss(toast.id)} aria-label="Dismiss message"><X size={14} /></button>
    </div>
  );
}

export function ToastStack() {
  const toasts = useWorldStore((state) => state.toasts);
  return <div className="toast-stack" aria-live="polite">{toasts.map((toast) => <Toast key={toast.id} toast={toast} />)}</div>;
}
