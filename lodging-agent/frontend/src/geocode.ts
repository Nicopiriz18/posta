import type { LatLng } from "./types";

// Geocodificación en el navegador con Nominatim (OpenStreetMap). Sin clave; se cachea en memoria por sesión.

const NOMINATIM = "https://nominatim.openstreetmap.org";

const searchCache = new Map<string, Promise<LatLng | null>>();
const reverseCache = new Map<string, Promise<string | null>>();

interface SearchRow {
  lat?: string;
  lon?: string;
}

interface ReverseRow {
  address?: Record<string, string | undefined>;
}

function num(s: string | undefined): number | null {
  if (s == null) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Centro aproximado de un destino ("Buenos Aires, Argentina"). `null` si no se pudo ubicar o no hay red. */
export function geocodeDestination(query: string): Promise<LatLng | null> {
  const key = query.trim().toLowerCase();
  if (!key) return Promise.resolve(null);
  const hit = searchCache.get(key);
  if (hit) return hit;
  const p = fetch(`${NOMINATIM}/search?format=json&limit=1&q=${encodeURIComponent(query.trim())}`)
    .then((r) => (r.ok ? (r.json() as Promise<SearchRow[]>) : null))
    .then((rows) => {
      const row = Array.isArray(rows) ? rows[0] : null;
      const lat = num(row?.lat);
      const lng = num(row?.lon);
      return lat != null && lng != null ? { lat, lng } : null;
    })
    .catch(() => null);
  searchCache.set(key, p);
  void p.then((v) => {
    if (v == null) searchCache.delete(key); // no cacheamos fallos: puede haber sido la red
  });
  return p;
}

/** Nombre de barrio para un punto (barrio → suburbio → distrito → ciudad). `null` si no hay nada útil. */
export function reverseZoneName(point: LatLng): Promise<string | null> {
  const key = `${point.lat.toFixed(4)},${point.lng.toFixed(4)}`;
  const hit = reverseCache.get(key);
  if (hit) return hit;
  const p = fetch(`${NOMINATIM}/reverse?format=json&zoom=16&lat=${point.lat}&lon=${point.lng}`)
    .then((r) => (r.ok ? (r.json() as Promise<ReverseRow>) : null))
    .then((row) => {
      const a = row?.address ?? {};
      const name = a.neighbourhood ?? a.suburb ?? a.city_district ?? a.quarter ?? a.borough ?? a.town ?? a.village ?? a.city ?? null;
      return name?.trim() || null;
    })
    .catch(() => null);
  reverseCache.set(key, p);
  void p.then((v) => {
    if (v == null) reverseCache.delete(key);
  });
  return p;
}

/** Centroide simple (promedio de vértices): alcanza para zonas de barrio. */
export function centroidOf(polygon: LatLng[]): LatLng | null {
  if (!polygon.length) return null;
  const sum = polygon.reduce((acc, p) => ({ lat: acc.lat + p.lat, lng: acc.lng + p.lng }), { lat: 0, lng: 0 });
  return { lat: sum.lat / polygon.length, lng: sum.lng / polygon.length };
}
