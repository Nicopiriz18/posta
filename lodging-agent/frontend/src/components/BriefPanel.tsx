import { useEffect, useState, type KeyboardEvent } from "react";
import { nightsBetween, type BriefDraft, type Zone } from "../types";
import { budgetLabel, dateRange, plural, travelersLabel } from "../format";

export const GROUPS = 6;

/** Cuántos de los 6 grupos (destino, fechas, viajeros, presupuesto, imprescindibles, preferencias) ya tienen algo. */
export function filledGroups(d: BriefDraft): number {
  let n = 0;
  if (d.destination?.trim()) n += 1;
  if (d.check_in && d.check_out) n += 1;
  if (d.adults != null) n += 1;
  if (d.budget_total_max != null || d.budget_per_night_max != null) n += 1;
  if (d.hard_constraints.length) n += 1;
  if (d.soft_preferences.length || d.area_preferences.length || d.dealbreakers.length || d.property_types.length || d.zone) n += 1;
  return n;
}

/** "Palermo · 5 vértices · tolerancia 300 m" */
export function zoneLabel(z: Zone): string {
  return `${z.name?.trim() || "Zona marcada"} · ${plural(z.polygon.length, "vértice", "vértices")} · tolerancia ${z.buffer_m} m`;
}

type RowKey = "destination" | "dates" | "travelers" | "musts" | "budget" | "prefs" | "areas" | "zone" | "dealbreakers";

const ROW_LABEL: Record<RowKey, string> = {
  destination: "Destino",
  dates: "Fechas",
  travelers: "Viajeros",
  musts: "Imprescindibles",
  budget: "Presupuesto",
  prefs: "Preferencias",
  areas: "Zonas",
  zone: "Zona en el mapa",
  dealbreakers: "Descartes",
};

/** Campo del backend (brief_saved.missing) → fila del panel. */
const MISSING_ROW: Record<string, RowKey> = {
  destination: "destination",
  check_in: "dates",
  check_out: "dates",
  adults: "travelers",
  children: "travelers",
  children_ages: "travelers",
  budget_total_max: "budget",
  budget_per_night_max: "budget",
  currency: "budget",
  hard_constraints: "musts",
  soft_preferences: "prefs",
  area_preferences: "areas",
  dealbreakers: "dealbreakers",
};

interface Props {
  draft: BriefDraft;
  onChange: (next: BriefDraft) => void;
  /** "Buscar con este brief": salta la conversación y lanza POST /api/runs en un hilo nuevo. */
  onLaunch: () => void;
  launching: boolean;
  launchError: string | null;
  missing: string[];
  confirmed: boolean;
  readOnly: boolean;
  blocked: string | null;
  /** Editor de zona (vive en la vista del hilo): abierto o no, y si se puede dibujar. */
  mapOpen: boolean;
  onMapToggle: () => void;
  zoneEditable: boolean;
}

function numOrNull(s: string): number | null {
  if (s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Lista separada por comas; se confirma al salir del campo o con Enter. */
function ListInput({ id, values, onCommit, placeholder }: { id: string; values: string[]; onCommit: (v: string[]) => void; placeholder: string }) {
  const [text, setText] = useState(values.join(", "));
  useEffect(() => setText(values.join(", ")), [values]);
  const commit = () =>
    onCommit(
      text
        .split(/[,\n;]/)
        .map((s) => s.trim())
        .filter(Boolean),
    );
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    }
  };
  return <input id={id} className="field" value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={onKey} />;
}

export default function BriefPanel({ draft, onChange, onLaunch, launching, launchError, missing, confirmed, readOnly, blocked, mapOpen, onMapToggle, zoneEditable }: Props) {
  const [editing, setEditing] = useState<RowKey | null>(null);
  const set = <K extends keyof BriefDraft>(k: K, v: BriefDraft[K]) => onChange({ ...draft, [k]: v });
  const nights = nightsBetween(draft.check_in, draft.check_out);
  const datesInvalid = nights != null && nights <= 0;
  const complete = Boolean(draft.destination?.trim() && draft.check_in && draft.check_out) && !datesInvalid;
  const canLaunch = complete && !launching && !blocked && !readOnly;
  const missingRows = new Set(missing.map((m) => MISSING_ROW[m]).filter(Boolean));

  useEffect(() => {
    if (readOnly) setEditing(null);
  }, [readOnly]);

  const ages = draft.children_ages.length ? ` (${draft.children_ages.join(" y ")})` : "";
  const values: Record<RowKey, string | null> = {
    destination: draft.destination?.trim() || null,
    dates: draft.check_in && draft.check_out ? `${dateRange(draft.check_in, draft.check_out)}${nights != null && nights > 0 ? ` · ${plural(nights, "noche", "noches")}` : ""}` : null,
    travelers: travelersLabel(draft.adults, draft.children) ? `${travelersLabel(draft.adults, draft.children)}${ages}` : null,
    musts: draft.hard_constraints.length ? draft.hard_constraints.join(" · ") : null,
    budget: budgetLabel(draft),
    prefs: draft.soft_preferences.length ? draft.soft_preferences.join(" · ") : null,
    areas: draft.area_preferences.length ? draft.area_preferences.join(" · ") : null,
    zone: draft.zone ? zoneLabel(draft.zone) : null,
    dealbreakers: draft.dealbreakers.length ? draft.dealbreakers.join(" · ") : null,
  };

  const editor = (k: RowKey) => {
    switch (k) {
      case "destination":
        return <input id="bp-destination" className="field" value={draft.destination ?? ""} placeholder="Ciudad, país" autoFocus onChange={(e) => set("destination", e.target.value || null)} />;
      case "dates":
        return (
          <div className="field-row">
            <label className="field-lbl">
              Entrada
              <input className="field" type="date" value={draft.check_in ?? ""} autoFocus onChange={(e) => set("check_in", e.target.value || null)} />
            </label>
            <label className="field-lbl">
              Salida
              <input className="field" type="date" value={draft.check_out ?? ""} min={draft.check_in ?? undefined} aria-invalid={datesInvalid || undefined} onChange={(e) => set("check_out", e.target.value || null)} />
            </label>
            {datesInvalid && <span className="error field-row__note">La salida tiene que ser después de la entrada.</span>}
          </div>
        );
      case "travelers":
        return (
          <div className="field-row field-row--3">
            <label className="field-lbl">
              Adultos
              <input className="field" type="number" min={1} max={12} value={draft.adults ?? ""} autoFocus onChange={(e) => set("adults", numOrNull(e.target.value))} />
            </label>
            <label className="field-lbl">
              Chicos
              <input className="field" type="number" min={0} max={10} value={draft.children ?? ""} onChange={(e) => set("children", numOrNull(e.target.value))} />
            </label>
            <label className="field-lbl">
              Edades
              <ListInput
                id="bp-ages"
                values={draft.children_ages.map(String)}
                placeholder="6, 9"
                onCommit={(v) => set("children_ages", v.map(Number).filter((n) => Number.isInteger(n) && n >= 0 && n < 18))}
              />
            </label>
          </div>
        );
      case "budget":
        return (
          <div className="field-row field-row--3">
            <label className="field-lbl">
              Moneda
              <input className="field" value={draft.currency ?? ""} placeholder="USD" maxLength={3} autoFocus onChange={(e) => set("currency", e.target.value.toUpperCase() || null)} />
            </label>
            <label className="field-lbl">
              Tope total
              <input className="field" type="number" min={0} value={draft.budget_total_max ?? ""} onChange={(e) => set("budget_total_max", numOrNull(e.target.value))} />
            </label>
            <label className="field-lbl">
              Por noche
              <input className="field" type="number" min={0} value={draft.budget_per_night_max ?? ""} onChange={(e) => set("budget_per_night_max", numOrNull(e.target.value))} />
            </label>
          </div>
        );
      case "musts":
        return <ListInput id="bp-musts" values={draft.hard_constraints} placeholder="cocina, wifi, cerca del mar" onCommit={(v) => set("hard_constraints", v)} />;
      case "prefs":
        return <ListInput id="bp-prefs" values={draft.soft_preferences} placeholder="desayuno, pileta" onCommit={(v) => set("soft_preferences", v)} />;
      case "areas":
        return <ListInput id="bp-areas" values={draft.area_preferences} placeholder="Palermo, Recoleta" onCommit={(v) => set("area_preferences", v)} />;
      case "dealbreakers":
        return <ListInput id="bp-deal" values={draft.dealbreakers} placeholder="hostel, baño compartido" onCommit={(v) => set("dealbreakers", v)} />;
      case "zone":
        return null; // se edita en el mapa, no en línea
    }
  };

  const rows = Object.keys(ROW_LABEL) as RowKey[];

  return (
    <div className={`brief${confirmed ? " brief--confirmed" : ""}`}>
      <div className="brief__head">
        <span className="kicker">Lo que sabemos</span>
        {confirmed && <span className="pill pill--ok">confirmado</span>}
      </div>

      <dl className="brief__rows">
        {rows.map((k) => {
          const isEditing = editing === k;
          const val = values[k];
          if (k === "zone") {
            return (
              <div key={k} className="brow brow--zone">
                <dt className="brow__k">{ROW_LABEL[k]}</dt>
                <dd className="brow__v brow__v--zone">
                  <span className={val ? undefined : "brow__pending"}>{val ?? "sin zona"}</span>
                  {(zoneEditable || draft.zone) && (
                    <button type="button" className="link-btn brow__map" aria-expanded={mapOpen} onClick={onMapToggle}>
                      {mapOpen ? "Cerrar el mapa" : zoneEditable ? "Dibujar en el mapa" : "Ver en el mapa"}
                    </button>
                  )}
                </dd>
              </div>
            );
          }
          return (
            <div key={k} className={`brow${isEditing ? " is-editing" : ""}`}>
              <dt className="brow__k">
                {ROW_LABEL[k]}
                {missingRows.has(k) && !val && <span className="brow__missing"> · falta</span>}
              </dt>
              <dd className="brow__v">
                {isEditing ? (
                  <div className="brow__editor">
                    {editor(k)}
                    <button type="button" className="link-btn" onClick={() => setEditing(null)}>
                      Listo
                    </button>
                  </div>
                ) : readOnly ? (
                  <span className={val ? undefined : "brow__pending"}>{val ?? "— pendiente"}</span>
                ) : (
                  <button type="button" className={`brow__btn${val ? "" : " brow__pending"}`} onClick={() => setEditing(k)} aria-label={`Editar ${ROW_LABEL[k].toLowerCase()}`}>
                    {val ?? "— pendiente"}
                  </button>
                )}
              </dd>
            </div>
          );
        })}
      </dl>

      <hr className="rule" />
      <p className="brief__hint">{readOnly ? (confirmed ? "La búsqueda corre con estos datos." : "Se puede editar cuando el agente termine.") : "Tocá cualquier dato para corregirlo. Todo esto va al pedido de búsqueda."}</p>

      {!confirmed && (
        <div className="brief__actions">
          <button type="button" className="btn btn--outline btn--block" disabled={!canLaunch} onClick={onLaunch}>
            {launching ? "Abriendo la búsqueda…" : "Buscar con este brief"}
          </button>
          <span className="brief__note">{blocked ?? (complete ? "Atajo sin conversación: abre una búsqueda nueva con estos datos tal cual." : "El atajo necesita destino y fechas.")}</span>
          {launchError && (
            <span className="error" role="alert">
              {launchError}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
