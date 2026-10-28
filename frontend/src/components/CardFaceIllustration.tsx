import type { ReactNode } from 'react';
import type { CardFaceProps } from './CardFace';

/** Small printing plates for the default face, independent of card data and actions. */
export function CardFaceIllustration({ definition, fallback }: Pick<CardFaceProps, 'definition'> & { fallback?: ReactNode }) {
  const id = definition?.id, traits = definition?.traits ?? [];
  const symbol = <span className="card-face-symbol" data-material-layer="protected" data-material-region="icon" aria-hidden="true">{fallback}</span>;
  // A plugin's logo or chosen Agent symbol takes precedence over trait-based defaults.
  if (definition?.icon_url || (traits.includes('core.agent') && definition?.icon && definition.icon !== 'bot')) return symbol;
  let plate: ReactNode;
  let subject: string;
  if (id === 'agent' || traits.includes('core.agent')) {
    subject = 'agent';
    plate = <>
      <circle cx="100" cy="43" r="16" fill="currentColor" />
      <path d="M100 75 154 179H122L100 133 78 179H46Z" fill="currentColor" />
      <path d="M100 75V133L78 179H46Z" className="card-plate-tint" />
      <path d="m88 159 12-26 12 26" fill="none" stroke="var(--face-paper)" strokeWidth="1.4" />
    </>;
  } else if (id === 'oaw.barracks.summoner' || traits.includes('oaw.summoner')) {
    subject = 'summoning';
    plate = <>
      <path d="M95 129V66a35 35 0 0 1 70 0v63h-15V66a20 20 0 0 0-40 0v63Z" className="card-plate-tint" />
      <path d="M35 181v-63a35 35 0 0 1 70 0v63H90v-63a20 20 0 0 0-40 0v63Z" fill="currentColor" />
      <path d="m70 151 60-70" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="m115 99 10 13-10 13-10-13Z" fill="currentColor" />
    </>;
  } else if (id === 'sandbox' || traits.includes('core.sandbox')) {
    subject = 'sandbox';
    plate = <>
      <path d="m100 139 65-37v39l-65 38-65-38v-39Z" className="card-plate-tint" />
      <path d="m100 139 65-37v39l-65 38Z" fill="currentColor" />
      <path d="m35 102 65 37 65-37M35 102V64l65-38 65 38v38M100 26v38M35 64l65 38 65-38M100 102v37" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="m100 64 21 12-21 12-21-12Z" fill="currentColor" />
      <path d="m79 76 21 12v25l-21-12Z" className="card-plate-tint" />
      <path d="m100 88 21-12v25l-21 12Z" fill="currentColor" />
    </>;
  } else if (id === 'oaw.barracks' || traits.includes('ui.agent-barracks.v1')) {
    subject = 'barracks';
    plate = <>
      <g className="card-plate-tint"><circle cx="54" cy="83" r="12" /><path d="m54 107 31 66H23Z" />
        <circle cx="146" cy="83" r="12" /><path d="m146 107 31 66h-62Z" /></g>
      <circle cx="100" cy="48" r="15" fill="currentColor" /><path d="m100 77 42 96H58Z" fill="currentColor" />
      <path d="m100 125 21 48H79Z" fill="var(--face-paper)" />
    </>;
  } else if (id === 'text') {
    subject = 'text';
    plate = <>
      <path d="m46 48 89-16 23 131-89 16Z" className="card-plate-tint" />
      <path d="M61 49h65l27 28v103H61Z" fill="var(--face-paper)" stroke="currentColor" strokeWidth="1.5" />
      <path d="M126 49v28h27M78 105h57M78 119h57M78 133h36" fill="none" stroke="currentColor" strokeWidth="2" />
    </>;
  } else if (id === 'conversation') {
    subject = 'conversation';
    plate = <>
      <path d="M31 45h101v79H77l-28 24v-24H31Z" className="card-plate-tint" />
      <path d="M81 90h88v76h-18v23l-28-23H81Z" fill="currentColor" />
      <path d="M97 112h43M97 126h27" fill="none" stroke="var(--face-paper)" strokeWidth="2" />
    </>;
  } else if (id === 'image') {
    subject = 'image';
    plate = <>
      <circle cx="137" cy="59" r="20" className="card-plate-tint" />
      <path d="m25 161 58-102 56 102Z" fill="currentColor" />
      <path d="m91 161 40-70 43 70Z" className="card-plate-tint" />
      <path d="m69 84 14-25 14 25-15-8Z" fill="var(--face-paper)" />
    </>;
  } else {
    return symbol;
  }
  return <svg className="card-face-illustration" data-illustration={subject} viewBox="0 0 200 210" fill="none" aria-hidden="true">{plate}</svg>;
}
