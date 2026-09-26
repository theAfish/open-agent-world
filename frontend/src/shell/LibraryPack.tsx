import { t, useLocale } from "../i18n";
import { useEffect, useState, type CSSProperties } from "react";
import { ChevronRight } from "lucide-react";
import { CatalogIcon } from "../components/CatalogIcon";
import { useSurfaceTilt } from "../components/useSurfaceTilt";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { CardFinishLayer } from "../cards/CardFinishLayer";
import { libraryCardMetadata } from "./libraryCatalog";
import "./libraryPack.css";

export function LibraryPack({ pack, snapshot, onOpened, onBrowse, onInspect, status, contentCountKnown = true }: {
  pack: LibrarySnapshot["packs"][string]; snapshot: LibrarySnapshot;
  onOpened: (id: string) => void; onBrowse: (id: string) => void;
  onInspect?: () => void; status?: string; contentCountKnown?: boolean;
}) {
  useLocale();
  const library = useCardLibrary();
  const tilt = useSurfaceTilt(15);
  const [phase, setPhase] = useState<"idle" | "pending" | "revealing">("idle");
  const [revealedSnapshot, setRevealedSnapshot] = useState<LibrarySnapshot | null>(null);
  const [finishVisible, setFinishVisible] = useState(false);
  const [failedArtwork, setFailedArtwork] = useState<string | null>(null);
  const definition = { ...pack.definition, name: t(pack.definition.name), description: t(pack.definition.description) };
  const plugin = snapshot.plugins[definition.plugin_id];
  const available = snapshot.available_pack_ids.includes(definition.id);
  const canOpen = !library.busy && phase === "idle" && available && pack.owned && !pack.opened;
  const artwork = definition.artwork_url && definition.artwork_url !== failedArtwork ? definition.artwork_url : null;
  const presetCount = Object.values(snapshot.preset_pack_ids ?? {}).filter(ids => ids.includes(definition.id)).length;
  const revealSnapshot = phase === "revealing" ? revealedSnapshot : null;
  const cards = definition.cards.slice(0, 3).map(id => (revealSnapshot ?? snapshot).card_definitions[id]);
  const color = definition.accent_color ?? cards.find(card => card?.color)?.color ?? "#617b72";
  useEffect(() => {
    if (phase !== "revealing") return;
    // The printed face emerges first, then its saved material catches the light.
    const light = window.setTimeout(() => setFinishVisible(true), 720);
    // Also settles if motion is disabled, the tab is hidden, or an animation is interrupted.
    const timer = window.setTimeout(() => setPhase("idle"), 2100);
    return () => { window.clearTimeout(timer); window.clearTimeout(light); };
  }, [phase]);
  const openPack = async () => {
    if (!canOpen) return;
    setFinishVisible(false);
    setRevealedSnapshot(null);
    setPhase("pending");
    const saved = await library.edit({ action: "open_pack", id: definition.id });
    if (saved?.packs[definition.id]?.opened) {
      // Parent/store updates may lag this promise. Never reveal from stale props.
      setRevealedSnapshot(saved);
      setPhase("revealing");
      onOpened(definition.id);
    } else setPhase("idle");
  };
  const unavailable = !plugin?.installed ? t("Pack uninstalled") : !plugin.enabled ? t("Pack disabled") : !available ? t("Pack unavailable") : !pack.owned ? t("Not owned") : "";
  return <article aria-label={definition.name} data-pack-id={definition.id} className={`library-pack ${pack.opened || revealSnapshot ? "is-opened" : ""} is-${phase}`} style={{ "--pack-color": color } as CSSProperties}>
    <button className="pack-touch-area" {...tilt} aria-label={onInspect ? t('View pack {name}', { name: definition.name }) : `${pack.opened || !available ? t("View cards in") : t("Tear open")} ${definition.name}`}
      title={onInspect ? t('View pack {name}', { name: definition.name }) : phase === "pending" ? t("Opening…") : pack.opened ? t("View {v0} cards", { v0: String(definition.name) }) : unavailable || t("Open {v0}", { v0: String(definition.name) })}
      aria-busy={phase === "pending"} disabled={phase !== "idle" || (!pack.opened && (library.busy || !pack.owned))}
      onClick={() => onInspect ? onInspect() : pack.opened || !available ? onBrowse(definition.id) : void openPack()}>
      <span className="pack-shadow" aria-hidden="true" />
      <span className="pack-object" aria-hidden="true" onAnimationEnd={event => { if (event.target === event.currentTarget && event.animationName === "packDeflate") setPhase("idle"); }}>
        <span className="pack-back" />
        <span className="pack-mouth" />
        <span className="pack-card-pocket"><span className="pack-drawn-cards">{cards.map((card, index) => <span className="pack-drawn-card card-finish-surface" key={definition.cards[index]}
          style={{ "--card-offset": index - (cards.length - 1) / 2, "--collection-color": card?.color ?? color } as CSSProperties}>
          <CatalogIcon definition={card} size={23} /><strong>{card ? libraryCardMetadata(revealSnapshot ?? snapshot, card).label : definition.cards[index]}</strong><small>{t("COLLECTED CARD")}</small>
          {revealSnapshot && finishVisible && <CardFinishLayer finish={revealSnapshot.collection[definition.cards[index]]?.finish} quality="standard" reveal />}
        </span>)}</span></span>
        <span className="pack-facet pack-facet-left" />
        <span className="pack-facet pack-facet-right" />
        <span className="pack-facet pack-facet-top" />
        <span className="pack-facet pack-facet-bottom" />
        <span className="pack-wrapper">
          <span className="pack-print">
            {artwork ? <img className="pack-artwork" src={artwork} alt="" draggable={false} onError={() => setFailedArtwork(artwork)} /> : <span className="pack-guilloche" />}
            <span className="pack-edition">{t(plugin?.descriptor.name ?? definition.plugin_id)}</span>
            <span className="pack-emblem"><CatalogIcon definition={cards[0]} size={38} /></span>
            <span className="pack-title">{definition.name}</span>
            <span className="pack-subtitle">{definition.description || t("A collection of possibilities.")}</span>
            <span className="pack-print-footer"><b>{contentCountKnown ? String(definition.cards.length + presetCount).padStart(2, "0") : '—'} <small>{t("CARDS")}</small></b><span>{t("OPEN AGENT")}<br />{t("WORLD")}</span></span>
          </span>
          <span className="pack-foil" />
        </span>
        <span className="pack-bottom-seal" />
        <span className="pack-top-seal"><span>{t(onInspect ? 'VIEW PACK' : 'TEAR TO OPEN')}</span><ChevronRight size={10} /></span>
      </span>
    </button>
    {status && <span className="pack-inventory-status">{t(status)}</span>}
  </article>;
}
