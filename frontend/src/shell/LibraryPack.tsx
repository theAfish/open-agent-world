import { t, useLocale } from "../i18n";
import { useEffect, useState, type CSSProperties } from "react";
import { ChevronRight } from "lucide-react";
import { CatalogIcon } from "../components/CatalogIcon";
import { useSurfaceTilt } from "../components/useSurfaceTilt";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { libraryCardMetadata } from "./libraryCatalog";
import "./libraryPack.css";

export function LibraryPack({ pack, snapshot, onOpened, onBrowse }: {
  pack: LibrarySnapshot["packs"][string]; snapshot: LibrarySnapshot;
  onOpened: (id: string) => void; onBrowse: (id: string) => void;
}) {
  useLocale();
  const library = useCardLibrary();
  const tilt = useSurfaceTilt(15);
  const [phase, setPhase] = useState<"idle" | "pending" | "revealing">("idle");
  const [failedArtwork, setFailedArtwork] = useState<string | null>(null);
  const definition = { ...pack.definition, name: t(pack.definition.name), description: t(pack.definition.description) };
  const plugin = snapshot.plugins[definition.plugin_id];
  const available = snapshot.available_pack_ids.includes(definition.id);
  const canOpen = !library.busy && phase === "idle" && available && pack.owned && !pack.opened;
  const artwork = definition.artwork_url && definition.artwork_url !== failedArtwork ? definition.artwork_url : null;
  const cards = definition.cards.slice(0, 3).map(id => snapshot.card_definitions[id]);
  const color = definition.accent_color ?? cards.find(card => card?.color)?.color ?? "#617b72";
  useEffect(() => {
    if (phase !== "revealing") return;
    // Also settles if motion is disabled, the tab is hidden, or an animation is interrupted.
    const timer = window.setTimeout(() => setPhase("idle"), 2100);
    return () => window.clearTimeout(timer);
  }, [phase]);
  const openPack = async () => {
    if (!canOpen) return;
    setPhase("pending");
    const saved = await library.edit({ action: "open_pack", id: definition.id });
    if (saved?.packs[definition.id]?.opened) {
      setPhase("revealing");
      onOpened(definition.id);
    } else setPhase("idle");
  };
  const unavailable = !plugin?.installed ? t("Plugin uninstalled") : !plugin.enabled ? t("Plugin disabled") : !available ? t("Pack unavailable") : !pack.owned ? t("Not owned") : "";
  return <article aria-label={definition.name} data-pack-id={definition.id} className={`library-pack ${pack.opened ? "is-opened" : ""} is-${phase}`} style={{ "--pack-color": color } as CSSProperties}>
    <button className="pack-touch-area" {...tilt} aria-label={`${pack.opened || !available ? t("View cards in") : t("Tear open")} ${definition.name}`}
      title={phase === "pending" ? t("Opening…") : pack.opened ? t("View {v0} cards", { v0: String(definition.name) }) : unavailable || t("Open {v0}", { v0: String(definition.name) })}
      aria-busy={phase === "pending"} disabled={phase !== "idle" || (!pack.opened && (library.busy || !pack.owned))}
      onClick={() => pack.opened || !available ? onBrowse(definition.id) : void openPack()}>
      <span className="pack-shadow" aria-hidden="true" />
      <span className="pack-object" aria-hidden="true" onAnimationEnd={event => { if (event.target === event.currentTarget && event.animationName === "packDeflate") setPhase("idle"); }}>
        <span className="pack-back" />
        <span className="pack-mouth" />
        <span className="pack-card-pocket"><span className="pack-drawn-cards">{cards.map((card, index) => <span className="pack-drawn-card" key={definition.cards[index]}
          style={{ "--card-offset": index - (cards.length - 1) / 2, "--collection-color": card?.color ?? color } as CSSProperties}>
          <CatalogIcon definition={card} size={23} /><strong>{card ? libraryCardMetadata(snapshot, card).label : definition.cards[index]}</strong><small>{t("COLLECTED CARD")}</small>
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
            <span className="pack-print-footer"><b>{String(definition.cards.length).padStart(2, "0")} <small>{t("CARDS")}</small></b><span>{t("OPEN AGENT")}<br />{t("WORLD")}</span></span>
          </span>
          <span className="pack-foil" />
        </span>
        <span className="pack-bottom-seal" />
        <span className="pack-top-seal"><span>{t("TEAR TO OPEN")}</span><ChevronRight size={10} /></span>
      </span>
    </button>
  </article>;
}
