import { t, useLocale } from "../i18n";
import { useEffect, useState } from "react";

export function DeepLSettings() {
  useLocale();
  const [key, setKey] = useState("");
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    fetch("/api/library/deepl").then(async response => {
      if (!response.ok) throw new Error(t("无法读取 DeepL 配置"));
      const data = await response.json();
      if (active) { setReady(data.api_key_configured); setLoaded(true); }
    }).catch(error => { if (active) setMessage(String(error)); });
    return () => { active = false; };
  }, []);
  async function save() {
    setBusy(true);
    try {
      const response = await fetch("/api/library/deepl", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: key || null }) });
      if (!response.ok) throw new Error(t("保存失败，请重试"));
      setReady((await response.json()).api_key_configured);
      setKey(""); setMessage(t("已保存"));
    } catch (error) { setMessage(String(error)); }
    finally { setBusy(false); }
  }
  return <div className="settings-form">
    <div className="settings-page-heading"><h3>{t("DeepL")}</h3></div>
    <label className="field-label">{t("DeepL Free API key")} <input type="password" autoComplete="new-password" value={key} disabled={!loaded || busy} placeholder={ready ? t("已加密保存，留空保留") : t("输入 DeepL Free 密钥")} onChange={event => setKey(event.target.value)} />
    </label>
    <button type="button" className="primary-button" disabled={!loaded || busy || !key.trim()} onClick={() => void save()}>{busy ? t("保存中…") : t("保存 DeepL 连接")}</button>
    {message && <p role="status">{message}</p>}
  </div>;
}
