import { useState } from 'react';
import { Popover } from '@oaw/plugin-api';

interface City { zone: string; en: string; zh: string; country: [string,string] }
const cities: City[] = [
  {zone:'Asia/Shanghai',en:'Shanghai',zh:'上海',country:['China','中国']},
  {zone:'Asia/Hong_Kong',en:'Hong Kong',zh:'香港',country:['China','中国']},
  {zone:'Asia/Tokyo',en:'Tokyo',zh:'东京',country:['Japan','日本']},
  {zone:'Asia/Seoul',en:'Seoul',zh:'首尔',country:['South Korea','韩国']},
  {zone:'Asia/Singapore',en:'Singapore',zh:'新加坡',country:['Singapore','新加坡']},
  {zone:'Asia/Bangkok',en:'Bangkok',zh:'曼谷',country:['Thailand','泰国']},
  {zone:'Asia/Jakarta',en:'Jakarta',zh:'雅加达',country:['Indonesia','印度尼西亚']},
  {zone:'Asia/Kolkata',en:'Kolkata',zh:'加尔各答',country:['India','印度']},
  {zone:'Asia/Dubai',en:'Dubai',zh:'迪拜',country:['United Arab Emirates','阿联酋']},
  {zone:'Europe/London',en:'London',zh:'伦敦',country:['United Kingdom','英国']},
  {zone:'Europe/Paris',en:'Paris',zh:'巴黎',country:['France','法国']},
  {zone:'Europe/Berlin',en:'Berlin',zh:'柏林',country:['Germany','德国']},
  {zone:'Europe/Rome',en:'Rome',zh:'罗马',country:['Italy','意大利']},
  {zone:'Europe/Moscow',en:'Moscow',zh:'莫斯科',country:['Russia','俄罗斯']},
  {zone:'America/New_York',en:'New York',zh:'纽约',country:['United States','美国']},
  {zone:'America/Chicago',en:'Chicago',zh:'芝加哥',country:['United States','美国']},
  {zone:'America/Denver',en:'Denver',zh:'丹佛',country:['United States','美国']},
  {zone:'America/Los_Angeles',en:'Los Angeles',zh:'洛杉矶',country:['United States','美国']},
  {zone:'America/Vancouver',en:'Vancouver',zh:'温哥华',country:['Canada','加拿大']},
  {zone:'America/Toronto',en:'Toronto',zh:'多伦多',country:['Canada','加拿大']},
  {zone:'America/Mexico_City',en:'Mexico City',zh:'墨西哥城',country:['Mexico','墨西哥']},
  {zone:'America/Sao_Paulo',en:'São Paulo',zh:'圣保罗',country:['Brazil','巴西']},
  {zone:'America/Argentina/Buenos_Aires',en:'Buenos Aires',zh:'布宜诺斯艾利斯',country:['Argentina','阿根廷']},
  {zone:'Africa/Cairo',en:'Cairo',zh:'开罗',country:['Egypt','埃及']},
  {zone:'Africa/Johannesburg',en:'Johannesburg',zh:'约翰内斯堡',country:['South Africa','南非']},
  {zone:'Africa/Nairobi',en:'Nairobi',zh:'内罗毕',country:['Kenya','肯尼亚']},
  {zone:'Australia/Sydney',en:'Sydney',zh:'悉尼',country:['Australia','澳大利亚']},
  {zone:'Australia/Perth',en:'Perth',zh:'珀斯',country:['Australia','澳大利亚']},
  {zone:'Australia/Adelaide',en:'Adelaide',zh:'阿德莱德',country:['Australia','澳大利亚']},
  {zone:'Pacific/Auckland',en:'Auckland',zh:'奥克兰',country:['New Zealand','新西兰']},
  {zone:'Pacific/Honolulu',en:'Honolulu',zh:'檀香山',country:['United States','美国']},
];

function cityFor(zone: string): City {
  return cities.find(city=>city.zone===zone) ?? {zone,en:zone.split('/').at(-1)!.replaceAll('_',' '),zh:zone.split('/').at(-1)!.replaceAll('_',' '),country:['Local time','当地时间']};
}

function PlaceMark() {
  return <svg viewBox="0 0 20 20" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M15.5 8c0 4-5.5 8-5.5 8S4.5 12 4.5 8a5.5 5.5 0 0 1 11 0Z"/><circle cx="10" cy="8" r="1.8"/></svg>;
}

function CityMenu({timeZone,now,busy,zh,onSave,close}:{timeZone:string;now:number;busy:boolean;zh:boolean;onSave:(args:Record<string,unknown>)=>Promise<boolean>;close:()=>void}) {
  const [search,setSearch]=useState(''), [failed,setFailed]=useState(false);
  const query=search.trim().toLocaleLowerCase();
  const filtered=cities.filter(city=>[city.en,city.zh,city.zone,...city.country].join(' ').toLocaleLowerCase().includes(query));
  const choose=async(zone:string)=>{setFailed(false);if(await onSave({time_zone:zone})) close();else setFailed(true);};
  const local=cityFor(Intl.DateTimeFormat().resolvedOptions().timeZone);
  return <div className="time-city-menu">
    <header><strong>{zh?'选择城市':'Choose a city'}</strong><span>{zh?'世界时钟':'WORLD CLOCK'}</span></header>
    <input className="time-city-search" data-autofocus aria-label={zh?'搜索城市或地区':'Search cities or regions'} placeholder={zh?'搜索城市或地区':'Search cities or regions'} value={search} onChange={e=>setSearch(e.target.value)}/>
    {!query && <button className="time-city-local" disabled={busy} onClick={()=>void choose(local.zone)}><PlaceMark/>{zh?'本地':'Local'} · {zh?local.zh:local.en}</button>}
    <div className="time-city-list">{filtered.map(city=><button key={city.zone} disabled={busy} aria-pressed={timeZone===city.zone} aria-label={zh?city.zh:city.en} onClick={()=>void choose(city.zone)}>
      <span><strong>{zh?city.zh:city.en}</strong><small>{city.country[zh?1:0]}</small></span>
      <time>{new Intl.DateTimeFormat(zh?'zh-CN':'en-GB',{timeZone:city.zone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(now)}</time>
      <span className="time-city-check" aria-hidden="true">{timeZone===city.zone?'✓':''}</span>
    </button>)}</div>
    {!filtered.length && <p className="time-city-empty">{zh?'没有匹配的城市':'No matching cities'}</p>}
    {failed && <p className="time-city-empty">{zh?'未能保存，请重试':'Could not save. Try again.'}</p>}
  </div>;
}

export function CityPicker({timeZone,now,busy,zh,onSave}:{timeZone:string;now:number;busy:boolean;zh:boolean;onSave:(args:Record<string,unknown>)=>Promise<boolean>}) {
  const city=timeZone?cityFor(timeZone):null;
  const name=city?(zh?city.zh:city.en):(zh?'自定义时间':'Custom time');
  return <Popover label={zh?'城市与时区':'City and time zone'} className="time-city-picker" placement="bottom"
    trigger={<button className="time-city-chip nodrag nopan" aria-disabled={busy} onClick={event=>{if(busy) event.preventDefault();}} aria-label={`${zh?'选择城市':'Choose city'}: ${name}`}><PlaceMark/><span>{name}</span></button>}>
    {close=><CityMenu timeZone={timeZone} now={now} busy={busy} zh={zh} onSave={onSave} close={close}/>}
  </Popover>;
}
