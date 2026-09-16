# Contrato API backend ⇄ frontend

Backend: FastAPI en `http://localhost:8000`. Todo bajo `/api`. En dev, Vite proxea `/api` al backend.
En producción, FastAPI sirve `frontend/dist` en `/`.

Los tipos son los de `src/lodging/schemas.py` (Pydantic v2). El frontend los replica en `frontend/src/types.ts`.

**Modelo:** un único deep agent conversacional. El usuario chatea en un **hilo** (`thread`). El agente va guardando
el brief (`brief_saved`), pide confirmación, y recién cuando el usuario confirma corre la investigación
(discovery → analistas en paralelo → `publish_report`) **dentro del mismo turno**. Todo lo que pasa en un turno
llega por SSE como eventos; el reporte llega en el evento `run_finished`.

## Salud

`GET /api/health` → `{ "ok": true, "serpapi_configured": bool, "anthropic_configured": bool, "version": "0.1.0" }`

## Hilos de conversación

`POST /api/threads` → `201 ThreadSummary` (crea un hilo vacío). `503 {detail}` si faltan claves en `.env`.

`GET /api/threads` → `ThreadSummary[]` (más reciente primero)

```ts
interface ThreadSummary {
  thread_id: string; run_id: string;
  status: "idle" | "running" | "archived";   // archived = cargado de runs/, solo lectura
  created_at: string;
  destination: string | null; check_in: string | null; check_out: string | null;
  has_report: boolean; fit_top: number | null; results_count: number | null;
  prices_stale: boolean;            // reporte de más de 24 h: mostrar 'precios vencidos' y ofrecer rebuscar
  last_message: string;
}
```

`GET /api/threads/{thread_id}` → `ThreadDetail`
```ts
interface ThreadDetail extends ThreadSummary {
  messages: { role: "user" | "assistant"; content: string; ts: string }[];
  draft: BriefDraft;            // lo que el agente fue guardando (puede estar incompleto)
  brief: Brief | null;          // brief válido (destino + fechas) si ya existe
  confirmed: boolean;           // el usuario confirmó y la investigación puede/pudo correr
  report: FinalReport | null;   // último reporte publicado en este hilo
  error: string | null;
  events_count: number;
}
```

`DELETE /api/threads/{thread_id}` → `204`. Elimina la conversación del historial (y su carpeta `runs/<id>/` si
existe). `409` si el agente está respondiendo en ese hilo, `404` si no existe. También `DELETE /api/runs/{run_id}`.

`PUT /api/threads/{thread_id}/zone` body `Zone | null` → `200 ThreadDetail`. Guarda (o borra con `null`) la zona que el
usuario dibujó en el mapa. Es un dato del brief que el agente **no** puede fijar ni editar: solo llega desde la UI.
Emite un evento `brief_saved`. `409` si el hilo está archivado o el agente está respondiendo; `422` si el polígono
tiene menos de 3 vértices. Después de guardar la zona, conviene mandar un mensaje al agente
("Marqué en el mapa la zona donde quiero hospedarme: {name}") para que la incorpore al resumen.

```ts
interface Zone { name: string | null; polygon: { lat: number; lng: number }[]; buffer_m: number }  // buffer 0..20000 m
```

`POST /api/threads/{thread_id}/messages` body `{ "content": "texto del usuario" }` → `202 { thread_id, status: "running" }`.
El turno corre en background; escuchar el SSE. Errores: `404` hilo inexistente, `409` si el agente todavía está
respondiendo o el hilo está archivado, `503` si faltan claves.

`GET /api/threads/{thread_id}/events` → **Server-Sent Events**. Reproduce todo el historial del hilo y después sigue en
vivo (queda abierto entre turnos; manda `: keepalive` cada 15 s). Para un hilo `archived` manda el historial y cierra
con `event: done`. Abrir **un solo** `EventSource` por hilo y mantenerlo abierto mientras se muestra la vista.
Con `?once=1` el servidor cierra (`event: done`) al terminar el turno en curso, o de inmediato si el hilo está idle
(útil para clientes sin streaming persistente y para tests).

Cada evento:
```
event: <RunEvent.type>
data: <RunEvent JSON>

```

```ts
interface RunEvent {
  type: "assistant" | "suggestions" | "brief_saved" | "run_started" | "phase" | "tool_call" | "tool_result"
      | "subagent_started" | "subagent_finished" | "verdict" | "model" | "message" | "warning"
      | "run_finished" | "run_failed" | "turn_done";
  run_id: string; ts: string;               // ISO
  agent: "orchestrator" | "hotel-discovery" | "property-analyst" | "system";
  message: string;                          // texto humano, en español, para la timeline
  data: Record<string, unknown>;            // ver abajo por tipo
}
```

`data` por tipo:
- `assistant`: `{ role: "assistant" }`. `message` es la respuesta del agente al usuario → va a la conversación (no a la timeline).
- `suggestions`: `{ options: string[] }` (2-4 respuestas rápidas que el agente propone para su próxima pregunta; llega ANTES del `assistant` de ese turno). Mostrar como chips debajo del último mensaje del agente; al tocar una, se manda como mensaje del usuario. Desaparecen al mandar cualquier mensaje.
- `brief_saved`: `{ status: "ready" | "incomplete", missing: string[], brief?: Brief, draft?: BriefDraft, confirmed?: boolean }` → actualizar el panel de brief.
- `run_started`: `{ brief: Brief }` → el usuario confirmó; arranca la investigación (mostrar timeline).
- `phase`: `{ phase: "discovery" | "selection" | "analysis" | "consolidation" }`
- `tool_call`: `{ tool: "search_hotels" | "get_property_details" | "get_property_reviews" | "web_search" | "fetch_page" | "add_web_candidate", args: {...} }` (`web_search.args.query`, `fetch_page.args.url`)
- `tool_result`: `{ tool, ok: boolean, summary: string, error?: string, property_name?: string, results?: {title,url}[] }` (`results` solo en `web_search`)
- `subagent_started` / `subagent_finished`: `{ subagent: "hotel-discovery" | "property-analyst", agent_id: string, property_name?: string, shortlist?: number }`
- `verdict`: `{ verdict: PropertyVerdict }` (uno por analista, a medida que terminan)
- `model`: `{ model: string, seconds: number | null, input_tokens: number | null, output_tokens: number | null }` (telemetría; mostrar discreto o solo en el registro)
- `message`: notas de progreso del agente (`{ todos?: [...] }`) → timeline
- `warning`: `{ error?: string, retry?: boolean }` → timeline, destacado
- `run_finished`: `{ report: FinalReport, assertions: { ok: boolean, checks: {...} } | null }` → renderizar el reporte
- `run_failed`: `{ error: string, partial_report?: FinalReport }`
- `turn_done`: `{ status: "ok" | "timeout" | "failed", error?: string }` → el agente terminó de responder; habilitar el input.

Orden típico de un turno con investigación: `brief_saved` → `run_started` → `assistant` ("arranco") → `phase(discovery)` →
`subagent_started` → `tool_call`/`tool_result`… → `subagent_finished` → `phase(selection)` → `message` (lectura de
candidatos) → `phase(analysis)` → N × `subagent_started` → … → N × (`verdict`, `subagent_finished`) → `phase(consolidation)`
→ `run_finished` → `assistant` (mensaje final) → `turn_done`. Los eventos `model` se intercalan en cualquier momento.

## Modo headless (sin chat) e historial

`POST /api/runs` body `Brief` → `202 { run_id, thread_id, status: "running" }`. Crea un hilo y manda el brief ya
confirmado; el agente investiga sin preguntar. Los eventos van por `GET /api/runs/{id}/events` (mismo formato que los
hilos). `GET /api/runs` → `ThreadSummary[]` de hilos con brief o reporte (incluye los archivados de `runs/`).
`GET /api/runs/{id}` → `ThreadDetail` con `status: "running" | "finished" | "failed" | "idle"`.

## Tipos de dominio

```ts
interface BriefDraft {
  destination: string | null; area_preferences: string[];
  check_in: string | null; check_out: string | null;   // "YYYY-MM-DD"
  adults: number | null; children: number | null; children_ages: number[];
  currency: string | null; budget_total_max: number | null; budget_per_night_max: number | null;
  property_types: string[]; dealbreakers: string[]; hard_constraints: string[]; soft_preferences: string[];
  travel_purpose: string | null; notes: string | null; language: string | null; gl: string | null;
  zone: Zone | null;                  // dibujada en el mapa (ver PUT /zone)
}
// Brief = BriefDraft con destination/check_in/check_out obligatorios, defaults adults=2, children=0,
// currency="USD", language="es", más `nights` (computado, solo lectura).

interface FinalReport {
  run_id: string; generated_at: string; language: string;
  brief: Brief;
  summary: string;                    // 3-5 oraciones en brief.language
  ranking: { rank: number; rationale: string; verdict: PropertyVerdict }[];   // TODAS las recomendadas (recommend=true), mejor primero; sin top fijo (0..8)
  verdicts: PropertyVerdict[];        // todos los analizados (rankeados o no)
  discarded: { property_token: string | null; name: string; reason: string; stage: "discovery"|"orchestrator"|"analyst" }[];
  caveats: string[];
  stats: { candidates_found: number; shortlisted: number; analyzed: number; serpapi_calls: number;
           tool_errors: number; duration_seconds: number; timed_out: boolean;
           orchestrator_model: string; analyst_model: string };
}

interface PropertyVerdict {
  property_token: string; name: string;
  fit_score: number;                  // 0-100, por rúbrica
  recommend: boolean;
  hard_constraints: ConstraintCheck[];
  soft_preferences: ConstraintCheck[];
  pros: string[]; cons: string[]; red_flags: string[]; unknowns: string[];
  price: PriceEvidence | null;
  over_budget: boolean | null;
  reviews_summary: string; reviews_analyzed: number;
  location_summary: string;
  summary: string;
  link: string | null;
  facts: PropertyFacts | null;        // ficha, copiada por código desde Google Hotels (trazable)
}
interface PropertyFacts {
  type: string | null; hotel_class: number | null; overall_rating: number | null; reviews_count: number | null;
  location_rating: number | null; address: string | null;
  essential_info: string[];          // ej. ['Toda la casa', 'Capacidad para 6', '3 dormitorios']
  amenities: string[]; excluded_amenities: string[];
  nearby_places: { name: string; category: string | null; transportations: string[] }[];
  offers: { source: string; total: number | null; per_night: number | null; free_cancellation: boolean | null; link: string | null }[];
                                     // el mismo alojamiento en distintas fuentes: Google Hotels (Booking, Expedia, sitio directo…)
                                     // más las halladas en la web abierta (source = dominio, ej. 'ventosul.com.br', link = la página)
  source: "google_hotels" | "web";     // de dónde salió la propiedad
  url: string | null;                 // página de origen (candidatos web)
  zone_inside: boolean | null;        // dentro de la zona dibujada (+buffer); null = sin coordenadas
  zone_distance_m: number | null;     // metros al borde de la zona (0 adentro); calculado por código
  booking_url: string | null;         // MEJOR enlace para abrir/reservar ESTA propiedad (elegido por código):
                                      //   página propia (web) > oferta de un portal real (Booking, Expedia, sitio directo…) > link de Google
  booking_source: string | null;      // a qué sitio lleva booking_url (dominio o nombre de la fuente)
  google_hotels_url: string | null;   // búsqueda de la propiedad en Google Hotels, como fallback
  free_cancellation: boolean | null; check_in_time: string | null; check_out_time: string | null;
  latitude: number | null; longitude: number | null;
  review_categories: { name: string; total: number; positive: number; negative: number; neutral: number }[];
}
interface ConstraintCheck { constraint: string; status: "confirmed" | "violated" | "unknown"; evidence: string | null }
interface PriceEvidence { total: number; currency: string; per_night: number | null; source: string;
                          verified_at: string /* ISO */; free_cancellation: boolean | null }
```

**Fuentes:** Google Hotels vía SerpAPI (hoteles y alquileres; compara Booking, Expedia, sitios directos y más) + web abierta
(búsqueda web de Anthropic + lectura de páginas de inmobiliarias, posadas y sitios directos). Los candidatos web tienen
`property_token` con prefijo `web:`, `facts.source = "web"` y `facts.url`. Airbnb, Booking, Expedia, Hoteis.com y Tripadvisor
no se leen directamente (bloquean lectura automatizada); sus propiedades llegan por Google Hotels.

**Enlace exacto:** usar `facts.booking_url` como acción principal ("Ver el alojamiento" / "Reservar en {booking_source}").
`verdict.link` puede ser un comparador (vio, freecancellations, bluepillow): mostrarlo solo como secundario. Para candidatos web,
`booking_url` es la página propia de la propiedad; `fetch_page` ahora devuelve los `links` de cada página y discovery
registra las propiedades desde su página propia (no desde listados). Los comparadores están en `AGGREGATOR_DOMAINS`.

**Zona en el mapa:** cuando el brief tiene `zone`, los candidatos con coordenadas fuera del polígono (+`buffer_m`) se
descartan en código antes de analizarlos y aparecen en `report.discarded` con `stage: "discovery"` y motivo
"Fuera de {zona}: a N m del borde". Los que no tienen coordenadas (típicamente candidatos web) no se descartan:
`zone_inside = null`. Para el mapa de resultados usar `facts.latitude` / `facts.longitude`.

**Rebuscar:** para repetir una búsqueda con el mismo brief (precios vencidos), `POST /api/runs` con `report.brief` (sin `nights`) crea un hilo nuevo en modo headless.

**Regla de presentación de precios (no negociable):** siempre mostrar `"desde {currency} {total} vía {source}, verificado a las HH:MM"`
(HH:MM local a partir de `verified_at`). Nunca un precio sin fuente. Si `price` es `null`: "precio no disponible".

**Reviews:** `reviews_summary` ya viene parafraseado y agregado. Nunca se muestran reviews textuales.

**`unknowns`** se muestran como "sin verificar", nunca como pro ni como contra.
