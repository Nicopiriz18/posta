import { useEffect, useId, useRef, useState } from "react";
import type { LatLng, Zone } from "../types";
import { errorMessage } from "../api";
import { centroidOf, geocodeDestination, reverseZoneName } from "../geocode";
import { plural } from "../format";
import ZoneMap from "./ZoneMap";

interface Props {
  /** Zona guardada en el hilo (o null). */
  zone: Zone | null;
  destination: string | null;
  readOnly: boolean;
  /** PUT /zone con la zona (o null para quitarla). Rechaza con el error a mostrar. */
  onSave: (zone: Zone | null) => Promise<void>;
  onClose: () => void;
}

const BUFFERS = [0, 300, 500, 1000];
const NO_GEO = "No pude ubicar el destino, movete en el mapa.";

function sameZone(a: Zone | null, b: Zone | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** "Palermo · 18 vértices · tolerancia 300 m" */
export function zoneSummary(z: Zone): string {
  return `${z.name?.trim() || "Zona marcada"} · ${plural(z.polygon.length, "vértice", "vértices")} · tolerancia ${z.buffer_m} m`;
}

/**
 * Editor de zona: tarjeta ancha dentro de la columna de la conversación, como un mensaje de la app.
 * Arriba, nombre + tolerancia + acciones; abajo, el mapa (los controles de dibujo flotan sobre él).
 */
export default function ZoneEditor({ zone, destination, readOnly, onSave, onClose }: Props) {
  const nameId = useId();
  const bufferId = useId();
  const [polygon, setPolygon] = useState<LatLng[] | null>(zone?.polygon ?? null);
  const [name, setName] = useState(zone?.name ?? "");
  const [buffer, setBuffer] = useState(zone?.buffer_m ?? 300);
  const [center, setCenter] = useState<LatLng | null>(null);
  const [geoNote, setGeoNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "clear" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const nameTouched = useRef(Boolean(zone?.name));
  const rootRef = useRef<HTMLElement>(null);

  // Si la zona guardada cambia de contenido desde afuera (SSE, guardado), el editor se alinea; un mismo contenido no pisa lo pendiente.
  const zoneKey = JSON.stringify(zone);
  useEffect(() => {
    setPolygon(zone?.polygon ?? null);
    setName(zone?.name ?? "");
    setBuffer(zone?.buffer_m ?? 300);
    nameTouched.current = Boolean(zone?.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneKey]);

  // Al abrirse entra a la vista y toma el foco (sin volver a desplazar).
  useEffect(() => {
    const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    rootRef.current?.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
    rootRef.current?.focus({ preventScroll: true });
  }, []);

  // Centro del mapa: el destino del brief, geocodificado con un pequeño retardo.
  useEffect(() => {
    const q = destination?.trim();
    if (!q) {
      setGeoNote(null);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(() => {
      void geocodeDestination(q).then((c) => {
        if (cancelled) return;
        setCenter(c);
        setGeoNote(c ? null : NO_GEO);
      });
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [destination]);

  const onPolygon = (next: LatLng[] | null) => {
    const hadPolygon = polygon != null;
    setPolygon(next);
    setError(null);
    if (!next) return;
    // Al cerrar un polígono nuevo, proponemos el barrio del centroide (editable).
    if (hadPolygon || nameTouched.current) return;
    const c = centroidOf(next);
    if (!c) return;
    void reverseZoneName(c).then((n) => {
      if (n && !nameTouched.current) setName(n);
    });
  };

  const pending: Zone | null = polygon && polygon.length >= 3 ? { name: name.trim() || null, polygon, buffer_m: buffer } : null;
  const dirty = !sameZone(pending, zone);

  const save = async () => {
    if (!pending) return;
    setBusy("save");
    setError(null);
    try {
      await onSave(pending);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  const clear = async () => {
    setBusy("clear");
    setError(null);
    try {
      await onSave(null);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const mapZone: Zone | null = pending ?? (polygon && polygon.length >= 3 ? { name: null, polygon, buffer_m: buffer } : null);

  return (
    <section ref={rootRef} className="zone-editor" aria-label="Zona en el mapa" tabIndex={-1}>
      <div className="zone-editor__bar">
        <span className="kicker zone-editor__title">Zona en el mapa</span>
        {!readOnly && (
          <>
            <label className="field-lbl zone-editor__name" htmlFor={nameId}>
              Nombre de la zona
              <input
                id={nameId}
                className="field"
                value={name}
                placeholder="Palermo Soho"
                maxLength={80}
                onChange={(e) => {
                  nameTouched.current = true;
                  setName(e.target.value);
                }}
              />
            </label>
            <label className="field-lbl zone-editor__buffer" htmlFor={bufferId}>
              Tolerancia alrededor de la zona
              <select id={bufferId} className="field" value={buffer} onChange={(e) => setBuffer(Number(e.target.value))}>
                {BUFFERS.map((b) => (
                  <option key={b} value={b}>
                    {b === 0 ? "Sin tolerancia" : `${b} m`}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <div className="zone-editor__actions">
          {!readOnly && (
            <button type="button" className="btn btn--accent btn--sm" disabled={!pending || !dirty || busy != null} onClick={() => void save()}>
              {busy === "save" ? "Guardando…" : "Guardar zona"}
            </button>
          )}
          {!readOnly && zone && (
            <button type="button" className="btn btn--outline btn--sm" disabled={busy != null} onClick={() => void clear()}>
              {busy === "clear" ? "Quitando…" : "Quitar zona"}
            </button>
          )}
          <button type="button" className="btn btn--outline btn--sm" onClick={onClose}>
            Cerrar
          </button>
        </div>
      </div>

      <ZoneMap center={center} zoom={13} zone={mapZone} onZoneChange={readOnly ? undefined : onPolygon} readOnly={readOnly} height={520} note={geoNote} className="zone-editor__map" />

      <div className="zone-editor__foot">
        <p className="zone-editor__note">
          {readOnly
            ? `${zone ? zoneSummary(zone) + "." : "Sin zona marcada."} Se puede editar cuando el agente termine.`
            : "Solo se buscan alojamientos dentro de la zona (más la tolerancia). Al guardar, le avisamos al agente."}
        </p>
        {error && (
          <span className="error" role="alert">
            {error}
          </span>
        )}
      </div>
    </section>
  );
}
