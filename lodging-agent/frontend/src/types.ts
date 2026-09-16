// Espejo de src/lodging/schemas.py y docs/api.md. Cambiar acá = cambio de contrato.

export type Role = "user" | "assistant";

/* ---------- Zona en el mapa ---------- */

export interface LatLng {
  lat: number;
  lng: number;
}

/**
 * Zona dibujada por el usuario en el mapa (PUT /api/threads/{id}/zone). El agente no puede fijarla ni editarla.
 * `polygon` tiene 3 o más vértices; `buffer_m` es la tolerancia alrededor del borde (0..20000 m).
 */
export interface Zone {
  name: string | null;
  polygon: LatLng[];
  buffer_m: number;
}

/** Mensaje de la conversación tal como lo guarda el backend (GET /api/threads/{id}). */
export interface ThreadMessage {
  role: Role;
  content: string;
  ts: string; // ISO
}

/** Brief parcial mientras la conversación lo completa. Todo opcional. */
export interface BriefDraft {
  destination: string | null;
  area_preferences: string[];
  check_in: string | null; // "YYYY-MM-DD"
  check_out: string | null;
  adults: number | null;
  children: number | null;
  children_ages: number[];
  currency: string | null;
  budget_total_max: number | null;
  budget_per_night_max: number | null;
  property_types: string[];
  dealbreakers: string[];
  hard_constraints: string[];
  soft_preferences: string[];
  travel_purpose: string | null;
  notes: string | null;
  language: string | null;
  gl: string | null;
  zone: Zone | null; // dibujada en el mapa; solo la UI la cambia (ver PUT /zone)
}

/** Brief válido: destino + fechas obligatorios. Es lo que se manda a POST /api/runs. `nights` lo computa el backend. */
export interface Brief {
  destination: string;
  area_preferences?: string[];
  check_in: string;
  check_out: string;
  adults?: number;
  children?: number;
  children_ages?: number[];
  currency?: string;
  budget_total_max?: number | null;
  budget_per_night_max?: number | null;
  property_types?: string[];
  dealbreakers?: string[];
  hard_constraints?: string[];
  soft_preferences?: string[];
  travel_purpose?: string | null;
  notes?: string | null;
  language?: string;
  gl?: string | null;
  zone?: Zone | null;
  readonly nights?: number;
}

export interface Health {
  ok: boolean;
  serpapi_configured: boolean;
  anthropic_configured: boolean;
  version: string;
}

/* ---------- Hilos ---------- */

/** archived = cargado de runs/, solo lectura. GET /api/runs/{id} además puede decir finished | failed. */
export type ThreadStatus = "idle" | "running" | "archived" | "finished" | "failed";

export interface ThreadSummary {
  thread_id: string;
  run_id: string;
  status: ThreadStatus;
  created_at: string;
  destination: string | null;
  check_in: string | null;
  check_out: string | null;
  has_report: boolean;
  fit_top: number | null;
  results_count: number | null; // largo de report.ranking (null sin reporte)
  prices_stale: boolean; // reporte de más de 24 h: "precios vencidos" + Rebuscar
  last_message: string;
}

export interface ThreadDetail extends ThreadSummary {
  messages: ThreadMessage[];
  draft: BriefDraft;
  brief: Brief | null;
  confirmed: boolean;
  report: FinalReport | null;
  error: string | null;
  events_count: number;
}

/** POST /api/threads/{id}/messages → 202 */
export interface MessageAccepted {
  thread_id: string;
  status: "running";
}

/** POST /api/runs → 202 (modo headless: crea un hilo con el brief ya confirmado). */
export interface RunCreated {
  run_id: string;
  thread_id: string;
  status: "running";
}

/* ---------- Reporte ---------- */

export type CheckStatus = "confirmed" | "violated" | "unknown";

export interface ConstraintCheck {
  constraint: string;
  status: CheckStatus;
  evidence: string | null;
}

export interface PriceEvidence {
  total: number;
  currency: string;
  per_night: number | null;
  source: string;
  verified_at: string; // ISO
  free_cancellation: boolean | null;
}

export interface PropertyVerdict {
  property_token: string;
  name: string;
  fit_score: number; // 0-100
  recommend: boolean;
  hard_constraints: ConstraintCheck[];
  soft_preferences: ConstraintCheck[];
  pros: string[];
  cons: string[];
  red_flags: string[];
  unknowns: string[];
  price: PriceEvidence | null;
  over_budget: boolean | null;
  reviews_summary: string;
  reviews_analyzed: number;
  location_summary: string;
  summary: string;
  link: string | null;
  facts: PropertyFacts | null; // ficha copiada por código desde el tool output (Google Hotels o página web), trazable
}

/** De dónde salió la propiedad. Los candidatos web tienen `property_token` con prefijo `web:`. */
export type PropertySource = "google_hotels" | "web";

export interface NearbyPlace {
  name: string;
  category: string | null;
  transportations: string[]; // ej. ["Taxi 14 min", "Transporte público 18 min"]
}

/**
 * El mismo alojamiento en otra fuente. Vía Google Hotels: Booking, Expedia, sitio directo…
 * Halladas en la web abierta: `source` es el dominio (ej. "ventosul.com.br") y `link` la página.
 */
export interface PriceOffer {
  source: string;
  total: number | null;
  per_night: number | null;
  free_cancellation: boolean | null;
  link: string | null;
}

export interface ReviewCategory {
  name: string;
  total: number;
  positive: number;
  negative: number;
  neutral: number;
}

export interface PropertyFacts {
  type: string | null;
  hotel_class: number | null;
  overall_rating: number | null;
  reviews_count: number | null;
  location_rating: number | null;
  address: string | null;
  essential_info: string[];
  amenities: string[];
  excluded_amenities: string[];
  nearby_places: NearbyPlace[];
  offers: PriceOffer[];
  source: PropertySource;
  url: string | null; // página de origen (candidatos web)
  /**
   * MEJOR enlace para abrir/reservar ESTA propiedad (lo elige el backend): página propia (web) > oferta de un portal
   * real (Booking, Expedia, sitio directo…) > comparador como último recurso. Acción principal de la UI.
   */
  booking_url: string | null;
  booking_source: string | null; // a qué sitio lleva booking_url (dominio o nombre de la fuente)
  google_hotels_url: string | null; // búsqueda de la propiedad en Google Hotels, fallback si no hay booking_url
  free_cancellation: boolean | null;
  check_in_time: string | null;
  check_out_time: string | null;
  latitude: number | null;
  longitude: number | null;
  zone_inside: boolean | null; // dentro de la zona dibujada (+buffer); null = sin coordenadas o sin zona
  zone_distance_m: number | null; // metros al borde de la zona (0 adentro); lo calcula el backend
  review_categories: ReviewCategory[];
}

export interface RankedProperty {
  rank: number;
  rationale: string;
  verdict: PropertyVerdict;
}

export type DiscardStage = "discovery" | "orchestrator" | "analyst";

export interface DiscardedCandidate {
  property_token: string | null;
  name: string;
  reason: string;
  stage: DiscardStage;
}

export interface RunStats {
  candidates_found: number;
  shortlisted: number;
  analyzed: number;
  serpapi_calls: number;
  tool_errors: number;
  duration_seconds: number;
  timed_out: boolean;
  orchestrator_model: string;
  analyst_model: string;
}

export interface FinalReport {
  run_id: string;
  generated_at: string;
  language: string;
  brief: Brief;
  summary: string;
  ranking: RankedProperty[];
  verdicts: PropertyVerdict[];
  discarded: DiscardedCandidate[];
  caveats: string[];
  stats: RunStats;
}

/* ---------- Eventos (SSE) ---------- */

export type EventType =
  | "assistant"
  | "suggestions"
  | "brief_saved"
  | "run_started"
  | "phase"
  | "tool_call"
  | "tool_result"
  | "subagent_started"
  | "subagent_finished"
  | "verdict"
  | "model"
  | "message"
  | "warning"
  | "run_finished"
  | "run_failed"
  | "turn_done";

export type AgentName = "orchestrator" | "hotel-discovery" | "property-analyst" | "system";

export type Phase = "discovery" | "selection" | "analysis" | "consolidation";

export type ToolName =
  | "search_hotels"
  | "get_property_details"
  | "get_property_reviews"
  | "web_search" // args.query; result.results = {title,url}[]
  | "fetch_page" // args.url; summary "título (N chars)"
  | "add_web_candidate"; // summary "nombre vía dominio" o "unificado con X"

/** Un resultado de `web_search`. */
export interface WebSearchResult {
  title: string;
  url: string;
}

export type SubagentName = "hotel-discovery" | "property-analyst";

export type TurnStatus = "ok" | "timeout" | "failed";

export interface AssistantData {
  role: "assistant";
}
/** Respuestas rápidas para la próxima pregunta del agente; llega ANTES del `assistant` del mismo turno. */
export interface SuggestionsData {
  options: string[];
}
export interface BriefSavedData {
  status: "ready" | "incomplete";
  missing: string[];
  brief?: Brief;
  draft?: BriefDraft;
  confirmed?: boolean;
}
export interface RunStartedData {
  brief: Brief;
}
export interface PhaseData {
  phase: Phase;
}
export interface ToolCallData {
  tool: ToolName;
  args: Record<string, unknown>;
  agent_id?: string;
}
export interface ToolResultData {
  tool: ToolName;
  ok: boolean;
  summary: string;
  error?: string;
  property_name?: string;
  results?: WebSearchResult[]; // solo en web_search
  agent_id?: string;
}
export interface SubagentData {
  subagent: SubagentName;
  agent_id: string;
  property_name?: string;
  shortlist?: number;
}
export interface VerdictData {
  verdict: PropertyVerdict;
}
export interface ModelData {
  model: string;
  seconds: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
}
export interface MessageData {
  todos?: unknown[];
}
export interface WarningData {
  error?: string;
  retry?: boolean;
}
export interface RunAssertions {
  ok: boolean;
  checks: Record<string, unknown>;
}
export interface RunFinishedData {
  report: FinalReport;
  assertions: RunAssertions | null;
}
export interface RunFailedData {
  error: string;
  partial_report?: FinalReport;
}
export interface TurnDoneData {
  status: TurnStatus;
  error?: string;
}

interface BaseEvent<T extends EventType, D> {
  type: T;
  run_id: string;
  ts: string; // ISO
  agent: AgentName | string;
  message: string;
  data: D;
}

export type RunEvent =
  | BaseEvent<"assistant", AssistantData>
  | BaseEvent<"suggestions", SuggestionsData>
  | BaseEvent<"brief_saved", BriefSavedData>
  | BaseEvent<"run_started", RunStartedData>
  | BaseEvent<"phase", PhaseData>
  | BaseEvent<"tool_call", ToolCallData>
  | BaseEvent<"tool_result", ToolResultData>
  | BaseEvent<"subagent_started", SubagentData>
  | BaseEvent<"subagent_finished", SubagentData>
  | BaseEvent<"verdict", VerdictData>
  | BaseEvent<"model", ModelData>
  | BaseEvent<"message", MessageData>
  | BaseEvent<"warning", WarningData>
  | BaseEvent<"run_finished", RunFinishedData>
  | BaseEvent<"run_failed", RunFailedData>
  | BaseEvent<"turn_done", TurnDoneData>;

/** `event: done` — solo lo manda el backend para hilos archivados (historial completo, sin vivo). */
export interface DoneEvent {
  status: ThreadStatus | string;
}

export const EVENT_TYPES: EventType[] = [
  "assistant",
  "suggestions",
  "brief_saved",
  "run_started",
  "phase",
  "tool_call",
  "tool_result",
  "subagent_started",
  "subagent_finished",
  "verdict",
  "model",
  "message",
  "warning",
  "run_finished",
  "run_failed",
  "turn_done",
];

/* ---------- Helpers de brief ---------- */

export function emptyDraft(): BriefDraft {
  return {
    destination: null,
    area_preferences: [],
    check_in: null,
    check_out: null,
    adults: null,
    children: null,
    children_ages: [],
    currency: null,
    budget_total_max: null,
    budget_per_night_max: null,
    property_types: [],
    dealbreakers: [],
    hard_constraints: [],
    soft_preferences: [],
    travel_purpose: null,
    notes: null,
    language: null,
    gl: null,
    zone: null,
  };
}

/** Un Brief del backend (todos los campos presentes) como draft editable. */
export function briefToDraft(b: Brief): BriefDraft {
  return {
    ...emptyDraft(),
    destination: b.destination,
    area_preferences: b.area_preferences ?? [],
    check_in: b.check_in,
    check_out: b.check_out,
    adults: b.adults ?? null,
    children: b.children ?? null,
    children_ages: b.children_ages ?? [],
    currency: b.currency ?? null,
    budget_total_max: b.budget_total_max ?? null,
    budget_per_night_max: b.budget_per_night_max ?? null,
    property_types: b.property_types ?? [],
    dealbreakers: b.dealbreakers ?? [],
    hard_constraints: b.hard_constraints ?? [],
    soft_preferences: b.soft_preferences ?? [],
    travel_purpose: b.travel_purpose ?? null,
    notes: b.notes ?? null,
    language: b.language ?? null,
    gl: b.gl ?? null,
    zone: b.zone ?? null,
  };
}

/** Mismo criterio que BriefDraft.to_brief(): saca nulls y listas vacías; los defaults los pone el backend. */
export function draftToBrief(d: BriefDraft): Brief | null {
  if (!d.destination?.trim() || !d.check_in || !d.check_out) return null;
  const brief: Brief = {
    destination: d.destination.trim(),
    check_in: d.check_in,
    check_out: d.check_out,
  };
  if (d.area_preferences.length) brief.area_preferences = d.area_preferences;
  if (d.adults != null) brief.adults = d.adults;
  if (d.children != null) brief.children = d.children;
  if (d.children_ages.length) brief.children_ages = d.children_ages;
  if (d.currency) brief.currency = d.currency.toUpperCase();
  if (d.budget_total_max != null) brief.budget_total_max = d.budget_total_max;
  if (d.budget_per_night_max != null) brief.budget_per_night_max = d.budget_per_night_max;
  if (d.property_types.length) brief.property_types = d.property_types;
  if (d.dealbreakers.length) brief.dealbreakers = d.dealbreakers;
  if (d.hard_constraints.length) brief.hard_constraints = d.hard_constraints;
  if (d.soft_preferences.length) brief.soft_preferences = d.soft_preferences;
  if (d.travel_purpose) brief.travel_purpose = d.travel_purpose;
  if (d.notes) brief.notes = d.notes;
  if (d.language) brief.language = d.language;
  if (d.gl) brief.gl = d.gl;
  if (d.zone) brief.zone = d.zone;
  return brief;
}

export function nightsBetween(checkIn: string | null, checkOut: string | null): number | null {
  if (!checkIn || !checkOut) return null;
  const a = Date.parse(checkIn + "T00:00:00Z");
  const b = Date.parse(checkOut + "T00:00:00Z");
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((b - a) / 86_400_000);
}
