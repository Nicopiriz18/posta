import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { LatLng, Zone } from "../types";

/**
 * Mapa (Leaflet + OpenStreetMap) con la zona dibujada por el usuario y, opcionalmente, marcadores de alojamientos.
 * El dibujo es a mano alzada (sin leaflet-draw): en modo dibujo se mantiene apretado y se arrastra como un lazo;
 * al soltar, el trazo se cierra y se simplifica. Los vértices resultantes se pueden arrastrar para ajustar.
 * Escape cancela el trazo. Los íconos son `divIcon` (los PNG por defecto de Leaflet no sobreviven al bundler).
 */

export interface MapMarker {
  id: string;
  lat: number;
  lng: number;
  label: string; // número de puesto
  title: string;
  subtitle?: string; // línea de precio
  inside: boolean | null; // navy adentro, gris sin dato, naranja afuera
}

interface Props {
  center: LatLng | null;
  zoom?: number;
  zone: Zone | null;
  /** Polígono cerrado (3+ vértices) o `null` al borrar. Solo se llama si no es `readOnly`. */
  onZoneChange?: (polygon: LatLng[] | null) => void;
  markers?: MapMarker[];
  readOnly?: boolean;
  height: number;
  /** Texto bajo el mapa (ej. "No pude ubicar el destino, movete en el mapa"). */
  note?: string | null;
  className?: string;
}

const NAVY = "#14213d";
const ACCENT = "#d9542b";
const WORLD: L.LatLngTuple = [15, -30];
const STEP_PX = 3; // se toma un punto cada ~3 px de movimiento
const SIMPLIFY_PX = 4; // tolerancia de L.LineUtil.simplify (en px de pantalla)
const MAX_VERTICES = 200;
const MIN_AREA_PX = 600; // trazos más chicos que ~25×25 px se ignoran
const TOO_SMALL = "Dibujá un área más grande.";

function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function coarsePointer(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
}

function toTuple(p: LatLng): L.LatLngTuple {
  return [p.lat, p.lng];
}

function fromLatLng(ll: L.LatLng): LatLng {
  return { lat: Number(ll.lat.toFixed(6)), lng: Number(ll.lng.toFixed(6)) };
}

/** Área (px²) de un polígono en coordenadas de pantalla, por la fórmula del cordón. */
function areaPx(pts: L.Point[]): number {
  let a = 0;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) a += (pts[j].x + pts[i].x) * (pts[j].y - pts[i].y);
  return Math.abs(a / 2);
}

/** Tope de vértices: se conservan puntos repartidos parejo a lo largo del trazo. */
function capVertices<T>(pts: T[], max: number): T[] {
  if (pts.length <= max) return pts;
  const step = pts.length / max;
  const out: T[] = [];
  for (let i = 0; i < max; i++) out.push(pts[Math.min(pts.length - 1, Math.round(i * step))]);
  return out;
}

function vertexIcon(): L.DivIcon {
  return L.divIcon({ className: "zm-vertex", iconSize: [14, 14], iconAnchor: [7, 7] });
}

function pinIcon(m: MapMarker): L.DivIcon {
  const tone = m.inside === true ? "in" : m.inside === false ? "out" : "na";
  const el = document.createElement("span");
  el.textContent = m.label;
  return L.divIcon({ className: `zm-pin zm-pin--${tone}`, html: el.outerHTML, iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -14] });
}

function popupFor(m: MapMarker): HTMLElement {
  const box = document.createElement("div");
  box.className = "zm-popup";
  const t = document.createElement("strong");
  t.textContent = m.title;
  box.appendChild(t);
  if (m.subtitle) {
    const s = document.createElement("span");
    s.textContent = m.subtitle;
    box.appendChild(s);
  }
  return box;
}

export default function ZoneMap({ center, zoom = 13, zone, onZoneChange, markers, readOnly = false, height, note, className }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const zoneLayer = useRef<L.LayerGroup | null>(null);
  const markerLayer = useRef<L.LayerGroup | null>(null);
  const traceLayer = useRef<L.LayerGroup | null>(null);
  const tracePts = useRef<L.Point[]>([]); // puntos de pantalla del trazo en curso
  const traceLine = useRef<L.Polyline | null>(null);
  const traceFill = useRef<L.Polygon | null>(null);
  const drawingRef = useRef(false);
  const tracingRef = useRef(false);
  const pointerId = useRef<number | null>(null);
  const onChangeRef = useRef(onZoneChange);
  const needsFit = useRef(false);
  const fitRef = useRef<() => void>(() => undefined);

  const [drawing, setDrawing] = useState(false);
  const [tracing, setTracing] = useState(false);
  const [warn, setWarn] = useState<string | null>(null);
  onChangeRef.current = onZoneChange;

  const editable = !readOnly && Boolean(onZoneChange);
  const hasZone = Boolean(zone && zone.polygon.length >= 3);

  /* ----- modo dibujo (lazo) ----- */

  const setInteractions = (map: L.Map, on: boolean) => {
    const handlers = [map.dragging, map.touchZoom, map.scrollWheelZoom, map.boxZoom, map.doubleClickZoom];
    handlers.forEach((h) => (on ? h.enable() : h.disable()));
    map.doubleClickZoom.disable(); // nunca: un doble clic sobre la zona no debe acercar
  };

  const clearTrace = useCallback(() => {
    tracePts.current = [];
    traceLine.current = null;
    traceFill.current = null;
    traceLayer.current?.clearLayers();
  }, []);

  const stopDrawing = useCallback(() => {
    const map = mapRef.current;
    drawingRef.current = false;
    tracingRef.current = false;
    pointerId.current = null;
    setDrawing(false);
    setTracing(false);
    clearTrace();
    if (map) setInteractions(map, true);
    hostRef.current?.classList.remove("is-drawing");
  }, [clearTrace]);

  const startDrawing = () => {
    const map = mapRef.current;
    if (!map || !editable) return;
    drawingRef.current = true;
    setDrawing(true);
    setWarn(null);
    setInteractions(map, false);
    hostRef.current?.classList.add("is-drawing");
    if (hasZone) onChangeRef.current?.(null); // se dibuja de cero
    map.getContainer().focus({ preventScroll: true });
  };

  const clearZone = () => {
    if (drawingRef.current) stopDrawing();
    setWarn(null);
    onChangeRef.current?.(null);
  };

  /** Cierra el trazo: simplifica en px, limita vértices y avisa al padre. */
  const finishTrace = useCallback(() => {
    const map = mapRef.current;
    const pts = tracePts.current;
    tracingRef.current = false;
    pointerId.current = null;
    setTracing(false);
    if (!map) return;
    const simplified = pts.length >= 3 ? capVertices(L.LineUtil.simplify(pts, SIMPLIFY_PX), MAX_VERTICES) : [];
    if (simplified.length < 3 || areaPx(simplified) < MIN_AREA_PX) {
      clearTrace();
      setWarn(TOO_SMALL); // sigue en modo dibujo para volver a intentar
      return;
    }
    const polygon = simplified.map((p) => fromLatLng(map.containerPointToLatLng(p)));
    stopDrawing();
    onChangeRef.current?.(polygon);
  }, [clearTrace, stopDrawing]);

  /* ----- mapa ----- */

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const reduced = reducedMotion();
    const map = L.map(el, {
      doubleClickZoom: false,
      zoomAnimation: !reduced,
      fadeAnimation: !reduced,
      markerZoomAnimation: !reduced,
      worldCopyJump: true,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    map.setView(WORLD, 2);
    zoneLayer.current = L.layerGroup().addTo(map);
    markerLayer.current = L.layerGroup().addTo(map);
    traceLayer.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    // Lazo: pointerdown arranca el trazo, pointermove suma puntos cada ~3 px, pointerup cierra.
    const onDown = (e: PointerEvent) => {
      if (!drawingRef.current || !e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      pointerId.current = e.pointerId;
      tracingRef.current = true;
      setTracing(true);
      setWarn(null);
      clearTrace();
      const p = map.mouseEventToContainerPoint(e);
      tracePts.current = [p];
      const ll = map.containerPointToLatLng(p);
      traceLine.current = L.polyline([ll], { color: ACCENT, weight: 2.5, interactive: false }).addTo(traceLayer.current as L.LayerGroup);
      traceFill.current = L.polygon([ll], { stroke: false, fillColor: NAVY, fillOpacity: 0.1, interactive: false }).addTo(traceLayer.current as L.LayerGroup);
    };
    const onMove = (e: PointerEvent) => {
      if (!tracingRef.current || e.pointerId !== pointerId.current) return;
      e.preventDefault();
      const p = map.mouseEventToContainerPoint(e);
      const pts = tracePts.current;
      const last = pts[pts.length - 1];
      if (last && last.distanceTo(p) < STEP_PX) return;
      pts.push(p);
      const ll = map.containerPointToLatLng(p);
      traceLine.current?.addLatLng(ll);
      traceFill.current?.addLatLng(ll);
    };
    const onUp = (e: PointerEvent) => {
      if (!tracingRef.current || e.pointerId !== pointerId.current) return;
      e.preventDefault();
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
      finishTrace();
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);

    // La tarjeta se monta en medio del flujo y anima su entrada: cada cambio de tamaño recalcula el mapa y, si el
    // encuadre quedó pendiente, lo aplica. El timer cubre el final de la animación.
    needsFit.current = true;
    const ro = new ResizeObserver(() => {
      map.invalidateSize({ animate: false });
      if (needsFit.current && el.clientHeight > 0 && el.clientWidth > 0) fitRef.current();
    });
    ro.observe(el);
    const settle = window.setTimeout(() => {
      map.invalidateSize({ animate: false });
      if (needsFit.current) fitRef.current();
    }, 320);
    return () => {
      window.clearTimeout(settle);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
      ro.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, [clearTrace, finishTrace]);

  // Escape cancela el trazo en curso (y sale del modo dibujo).
  useEffect(() => {
    if (!drawing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        stopDrawing();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, stopDrawing]);

  // Si el mapa pasa a solo lectura mientras se dibuja, se cancela.
  useEffect(() => {
    if (!editable && drawingRef.current) stopDrawing();
  }, [editable, stopDrawing]);

  /* ----- zona guardada / pendiente ----- */

  const polygonKey = zone ? zone.polygon.map((p) => `${p.lat},${p.lng}`).join(";") : "";
  useEffect(() => {
    const layer = zoneLayer.current;
    if (!layer) return;
    layer.clearLayers();
    if (!zone || zone.polygon.length < 3) return;
    const latlngs = zone.polygon.map(toTuple);
    const poly = L.polygon(latlngs, { color: NAVY, weight: 2, fillColor: NAVY, fillOpacity: 0.12, interactive: false }).addTo(layer);
    if (!editable) return;
    const current = zone.polygon.map((p) => L.latLng(p.lat, p.lng));
    current.forEach((ll, i) => {
      const m = L.marker(ll, { icon: vertexIcon(), draggable: true, keyboard: false, title: `Vértice ${i + 1}: arrastrá para ajustar` }).addTo(layer);
      m.on("drag", () => {
        current[i] = m.getLatLng();
        poly.setLatLngs(current);
      });
      m.on("dragend", () => {
        current[i] = m.getLatLng();
        onChangeRef.current?.(current.map(fromLatLng));
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polygonKey, editable]);

  /* ----- marcadores ----- */

  const markersKey = (markers ?? []).map((m) => `${m.id}:${m.lat},${m.lng}:${m.inside}`).join(";");
  useEffect(() => {
    const layer = markerLayer.current;
    if (!layer) return;
    layer.clearLayers();
    (markers ?? []).forEach((m) => {
      L.marker([m.lat, m.lng], { icon: pinIcon(m), title: m.title, alt: `${m.label}: ${m.title}` })
        .bindPopup(popupFor(m), { closeButton: false, offset: [0, 0] })
        .addTo(layer);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markersKey]);

  /* ----- encuadre ----- */

  fitRef.current = () => {
    const map = mapRef.current;
    const el = hostRef.current;
    if (!map || !el) return;
    if (drawingRef.current) return; // no mover el mapa debajo del trazo (borrar la zona para redibujar dispara esto)
    if (el.clientHeight === 0 || el.clientWidth === 0) {
      needsFit.current = true; // todavía plegado: encuadramos cuando tenga tamaño
      return;
    }
    needsFit.current = false;
    const bounds = L.latLngBounds([]);
    zone?.polygon.forEach((p) => bounds.extend(toTuple(p)));
    (markers ?? []).forEach((m) => bounds.extend([m.lat, m.lng]));
    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.15), { maxZoom: 16, animate: false });
    } else if (center) {
      map.setView(toTuple(center), zoom, { animate: false });
    } else {
      map.setView(WORLD, 2, { animate: false });
    }
  };
  // Encuadra cuando aparece o desaparece la zona, cambian los marcadores o el centro; no en cada vértice arrastrado.
  useEffect(() => {
    fitRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasZone, markersKey, center?.lat, center?.lng, zoom]);

  const hint = drawing
    ? tracing
      ? "Soltá para cerrar la zona."
      : (warn ?? (coarsePointer() ? "Mantené el dedo apretado y dibujá la zona." : "Mantené apretado y dibujá la zona en el mapa."))
    : hasZone
      ? editable
        ? "Arrastrá los puntos para ajustar la zona."
        : null
      : editable
        ? "Dibujá el área donde querés hospedarte."
        : null;

  return (
    <div className={`zmap${className ? ` ${className}` : ""}`}>
      <div
        ref={hostRef}
        className="zmap__canvas"
        style={{ "--zmap-h": `${height}px` } as CSSProperties}
        role="application"
        aria-label={editable ? "Mapa para dibujar la zona" : "Mapa de la zona"}
        tabIndex={editable ? 0 : -1}
      />
      {editable && (
        <div className="zmap__bar">
          {!drawing ? (
            <button type="button" className={`btn btn--sm ${hasZone ? "btn--outline" : "btn--navy"}`} onClick={startDrawing}>
              {hasZone ? "Dibujar de nuevo" : "Dibujar zona"}
            </button>
          ) : (
            <button type="button" className="btn btn--outline btn--sm" onClick={stopDrawing}>
              Cancelar
            </button>
          )}
          {hasZone && !drawing && (
            <button type="button" className="btn btn--outline btn--sm" onClick={clearZone}>
              Borrar zona
            </button>
          )}
        </div>
      )}
      {(hint || note) && (
        <p className="zmap__note" aria-live="polite">
          {[note, hint].filter(Boolean).join(" ")}
        </p>
      )}
    </div>
  );
}
