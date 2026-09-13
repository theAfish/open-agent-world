import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
function Settings({card,host}:PluginViewProps){
  useLocale();
  const [error,setError]=useState("");
  const [result,setResult]=useState<Record<string,unknown>>();
  const [archive,setArchive]=useState("");
  useEffect(()=>{let active=true;void host.getAgentInfo().then(info=>{if(active){setResult(info.details?.result as Record<string,unknown>|undefined);setArchive(String(info.details?.archive??""));}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[host,card.status]);
  const save=(patch:Record<string,unknown>)=>void host.updateConfig(patch).catch(e=>setError(String(e)));
  return <section className="expanded-stack nodrag nopan">
    <p>{t("PyWPEM · 候选 CIF 全谱拟合。下方 Run / Stop 使用 OAW 的运行管理。")}</p>
    <label><input type="checkbox" checked={Boolean(card.config.demo)} onChange={e=>save({demo:e.target.checked})}/>{t("使用官方 Mn₂O₃ 示例")}</label>
    {!card.config.demo&&<>{([['intensity_csv',t("实验谱：无表头两列 CSV"),'.csv,.txt'],['cif',t("候选结构 CIF"),'.cif']] as const).map(([key,label,accept])=><label key={key}>{label}<input type="file" accept={accept} onChange={async e=>{try{const f=e.target.files?.[0];if(f){if(f.size>8*1024*1024)throw new Error(t("文件最大 8 MiB"));save({[key]:await f.text()});}}catch(err){setError(String(err));}}}/><small>{card.config[key]?t("已载入"):t("尚未载入")}</small></label>)}</>}
    {([['iterations',t("最大迭代")],['wavelength',t("波长 / Å")],['low_angle',t("拟合下限 / °")],['high_angle',t("拟合上限 / °")]] as const).map(([key,label])=><label key={key}>{label}<input type="number" step="any" defaultValue={Number(card.config[key])} onBlur={e=>save({[key]:Number(e.target.value)})}/></label>)}
    <label><input type="checkbox" checked={Boolean(card.config.preoptimize)} onChange={e=>save({preoptimize:e.target.checked})}/>{t("先运行结构预优化")}</label>
    <small>{t("日志会给出结果目录、R 因子及 stop_flag；达到迭代上限不等于收敛。")}</small>
    {result&&<><pre style={{whiteSpace:'pre-wrap'}}>{t("结果：")}{JSON.stringify(result.solver,null,2)}{result.converged?t("\n达到收敛判据"):t("\n尚未达到收敛判据")}</pre>{archive?<a download="xrd-results.zip" href={`data:application/zip;base64,${archive}`}>{t("下载拟合结果（含曲线和 CIF）")}</a>:<small>{t("结果较大，请从运行日志中的本地目录读取。")}</small>}</>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
export default {apiVersion:1,views:{settings:Settings}} satisfies FrontendPlugin;
