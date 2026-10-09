import { t, useLocale } from "../i18n";
import { useEffect, useState } from "react";
import { CatalogIcon } from "../components/CatalogIcon";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { PackSurface, type PackPhase } from './PackSurface';
import { libraryCardMetadata } from "./libraryCatalog";
import { packIssue, type InstalledPack } from './installedPacks';
import { packPackaging } from './packDesign';

export function LibraryPack({ pack, snapshot, onOpened, onBrowse, onInspect, status, versions, contentCountKnown = true }: {
  pack: LibrarySnapshot["packs"][string]; snapshot: LibrarySnapshot;
  onOpened: (id: string) => void; onBrowse: (id: string) => void;
  onInspect?: () => void; status?: string; contentCountKnown?: boolean;
  versions?: InstalledPack[];
}) {
  useLocale();
  const library = useCardLibrary();
  const [phase, setPhase] = useState<PackPhase>("idle");
  const [revealedSnapshot, setRevealedSnapshot] = useState<LibrarySnapshot | null>(null);
  const [finishVisible, setFinishVisible] = useState(false);
  const definition = { ...pack.definition, name: t(pack.definition.name), description: t(pack.definition.description) };
  const plugin = snapshot.plugins[definition.plugin_id];
  const available = snapshot.available_pack_ids.includes(definition.id);
  const issue = packIssue(pack, snapshot, versions);
  const canOpen = !issue && !library.busy && phase === "idle" && available && pack.owned && !pack.opened;
  const presetCount = Object.values(snapshot.preset_pack_ids ?? {}).filter(ids => ids.includes(definition.id)).length;
  const revealSnapshot = phase === "revealing" ? revealedSnapshot : null;
  const cards = definition.cards.slice(0, 3).map(id => (revealSnapshot ?? snapshot).card_definitions[id]);
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
  const unavailable = issue ? t(issue.label) : !plugin?.installed ? t("Pack uninstalled") : !plugin.enabled ? t("Pack disabled") : !available ? t("Pack unavailable") : !pack.owned ? t("Not owned") : "";
  const packaging = packPackaging(definition.packaging);
  const openLabel = packaging === 'paper' || packaging === 'collector' ? t("Open {v0}", { v0: definition.name }) : `${t("Tear open")} ${definition.name}`;
  return <PackSurface definition={definition} edition={t(plugin?.descriptor.name ?? definition.plugin_id)}
    cards={cards.map((card, index) => ({ id: definition.cards[index],
      label: card ? libraryCardMetadata(revealSnapshot ?? snapshot, card).label : definition.cards[index],
      icon: <CatalogIcon definition={card} size={38} />, iconUrl: card?.icon_url ?? undefined, color: card?.color,
      finish: revealSnapshot?.collection[definition.cards[index]]?.finish,
    }))} count={contentCountKnown ? definition.cards.length + presetCount : null}
    opened={pack.opened || !!revealSnapshot} phase={phase} finishVisible={finishVisible}
    issue={issue?.kind} status={issue || status ? t(issue?.label ?? status!) : undefined}
    label={onInspect ? t('View pack {name}', { name: definition.name }) : pack.opened || !available || issue ? `${t("View cards in")} ${definition.name}` : openLabel}
    title={onInspect ? t('View pack {name}', { name: definition.name }) : phase === "pending" ? t("Opening…") : pack.opened ? t("View {v0} cards", { v0: definition.name }) : unavailable || t("Open {v0}", { v0: definition.name })}
    sealLabel={t(onInspect || issue || !available ? 'VIEW PACK' : 'TEAR TO OPEN')}
    disabled={phase !== "idle" || (!pack.opened && (library.busy || !pack.owned))}
    onClick={() => onInspect ? onInspect() : pack.opened || !available || issue ? onBrowse(definition.id) : void openPack()}
    onSettled={() => setPhase('idle')} />;
}
