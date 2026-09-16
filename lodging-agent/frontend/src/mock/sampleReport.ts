import { emptyDraft, type FinalReport, type PropertyFacts, type PropertyVerdict, type RunEvent, type ThreadDetail, type ThreadSummary, type Zone } from "../types";

// Conversación y reporte de ejemplo para revisar el diseño sin backend: `#/t/mock?mock=1`.
// Datos plausibles, no reales.

const VERIFIED = "2026-09-11T21:42:10+00:00";

const noFacts: PropertyFacts = {
  type: null,
  hotel_class: null,
  overall_rating: null,
  reviews_count: null,
  location_rating: null,
  address: null,
  essential_info: [],
  amenities: [],
  excluded_amenities: [],
  nearby_places: [],
  offers: [],
  source: "google_hotels",
  url: null,
  booking_url: null,
  booking_source: null,
  google_hotels_url: null,
  free_cancellation: null,
  check_in_time: null,
  check_out_time: null,
  latitude: null,
  longitude: null,
  zone_inside: null,
  zone_distance_m: null,
  review_categories: [],
};

// Zona dibujada en el mapa alrededor de Palermo (Buenos Aires). Los candidatos con coordenadas fuera se descartan por código.
const PALERMO_ZONE: Zone = {
  name: "Palermo",
  // Trazo a mano alzada, ya simplificado (así lo guarda el editor).
  polygon: [
    { lat: -34.56972, lng: -58.426 },
    { lat: -34.57145, lng: -58.41826 },
    { lat: -34.57577, lng: -58.41328 },
    { lat: -34.57952, lng: -58.40891 },
    { lat: -34.58413, lng: -58.40696 },
    { lat: -34.58876, lng: -58.40787 },
    { lat: -34.59402, lng: -58.40758 },
    { lat: -34.59975, lng: -58.41028 },
    { lat: -34.60087, lng: -58.4186 },
    { lat: -34.59872, lng: -58.426 },
    { lat: -34.59871, lng: -58.43228 },
    { lat: -34.59799, lng: -58.43963 },
    { lat: -34.59402, lng: -58.44442 },
    { lat: -34.58916, lng: -58.44734 },
    { lat: -34.58372, lng: -58.44825 },
    { lat: -34.57952, lng: -58.44309 },
    { lat: -34.57754, lng: -58.43663 },
    { lat: -34.57362, lng: -58.43263 },
  ],
  buffer_m: 300,
};

function facts(over: Partial<PropertyFacts>): PropertyFacts {
  return { ...noFacts, ...over };
}

const nido: PropertyVerdict = {
  property_token: "ChkI0aX9m5f3xJ8xGg0vZy8xMXJ4bDNqcXIQAQ",
  name: "Nido @ Palermo Soho Square",
  fit_score: 82,
  recommend: true,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "amenities de la ficha; 9 reseñas recientes lo mencionan como rápido" },
    { constraint: "cocina o kitchenette", status: "confirmed", evidence: "amenities: kitchenette en todas las unidades" },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "confirmed", evidence: "ficha: desayuno incluido en la tarifa" },
    { constraint: "cerca de Plaza Serrano", status: "confirmed", evidence: "lugares cercanos: Plaza Serrano a 4 min a pie" },
    { constraint: "terraza o pileta", status: "confirmed", evidence: "amenities: terraza con solárium" },
    { constraint: "check-in flexible", status: "confirmed", evidence: "check-in 24 h según la ficha" },
  ],
  pros: [
    "Kitchenette equipada en todas las unidades, útil para las 3 noches",
    "Varias reseñas recientes destacan la limpieza y la atención del staff",
    "A 4 minutos a pie de Plaza Serrano, sobre una calle secundaria",
  ],
  cons: ["Ruido de la calle los fines de semana, según dos reseñas"],
  red_flags: [],
  unknowns: ["Si hay ascensor (el edificio tiene 4 pisos y la ficha no lo indica)", "Política de cancelación de la tarifa mostrada"],
  price: { total: 468, currency: "USD", per_night: 156, source: "eDreams", verified_at: VERIFIED, free_cancellation: null },
  over_budget: false,
  reviews_summary:
    "De 25 reseñas leídas, la mayoría son muy positivas: destacan limpieza, la kitchenette y el trato del personal. Dos mencionan ruido de la calle los fines de semana; ninguna reporta problemas de wifi.",
  reviews_analyzed: 25,
  location_summary:
    "Palermo Soho, a 4 min a pie de Plaza Serrano y 10 min de la estación Plaza Italia (línea D). Zona con bares y restaurantes; ruidosa de noche los fines de semana.",
  summary:
    "Apart-hotel chico en pleno Palermo Soho que cumple las dos condiciones imprescindibles y las cuatro preferencias, dentro del presupuesto. La única reserva es el ruido nocturno de fin de semana.",
  link: "https://www.google.com/travel/hotels/entity/ChkI0aX9m5f3xJ8xGg0vZy8xMXJ4bDNqcXIQAQ",
  facts: facts({
    type: "Apart-hotel",
    overall_rating: 4.6,
    reviews_count: 312,
    location_rating: 4.8,
    address: "Thames 1800, Palermo Soho, Buenos Aires",
    latitude: -34.5887,
    longitude: -58.4302,
    zone_inside: true,
    zone_distance_m: 0,
    essential_info: ["Kitchenette en todas las unidades", "Terraza con solárium"],
    amenities: ["Wifi", "Kitchenette", "Desayuno", "Terraza", "Aire acondicionado"],
    excluded_amenities: ["Pileta", "Cochera"],
    nearby_places: [
      { name: "Plaza Serrano", category: "Plaza", transportations: ["A pie 4 min"] },
      { name: "Estación Plaza Italia", category: "Subte", transportations: ["A pie 10 min"] },
    ],
    offers: [
      { source: "eDreams", total: 468, per_night: 156, free_cancellation: null, link: "https://www.edreams.com/" },
      { source: "Booking.com", total: 492, per_night: 164, free_cancellation: true, link: "https://www.booking.com/hotel/ar/nido-palermo-soho-square.html" },
      // Hallada en la web abierta y unificada por nombre con el candidato de Google Hotels (add_web_candidate → "unificado con …").
      { source: "nidopalermo.com.ar", total: 480, per_night: 160, free_cancellation: true, link: "https://nidopalermo.com.ar/reservas" },
    ],
    // Enlace exacto elegido por el backend: portal real (Booking) antes que comparadores; eDreams sigue como "Reservar en".
    booking_url: "https://www.booking.com/hotel/ar/nido-palermo-soho-square.html",
    booking_source: "Booking.com",
    google_hotels_url: "https://www.google.com/travel/search?q=Nido%20%40%20Palermo%20Soho%20Square%20Buenos%20Aires",
    check_in_time: "14:00",
    check_out_time: "11:00",
  }),
};

const palermoSuites: PropertyVerdict = {
  property_token: "ChoIm7bA2Nq0kMR7GgkvbS8wMV9wcXcQAQ",
  name: "Palermo Suites Buenos Aires Hotel & Apartments",
  fit_score: 74,
  recommend: true,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "amenities de la ficha" },
    { constraint: "cocina o kitchenette", status: "confirmed", evidence: "amenities: cocina en los apartamentos" },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "confirmed", evidence: "ficha: desayuno buffet incluido" },
    { constraint: "cerca de Plaza Serrano", status: "confirmed", evidence: "lugares cercanos: Plaza Serrano a 9 min a pie" },
    { constraint: "terraza o pileta", status: "confirmed", evidence: "amenities: pileta exterior en la terraza" },
    { constraint: "check-in flexible", status: "unknown", evidence: null },
  ],
  pros: ["Pileta en la terraza, abierta en octubre según reseñas de 2025", "Desayuno buffet incluido, bien valorado", "Apartamentos amplios con cocina completa"],
  cons: ["El edificio muestra algo de desgaste, según varias reseñas"],
  red_flags: [],
  unknowns: ["Horario de check-in tardío", "Si la pileta está climatizada"],
  price: { total: 519, currency: "USD", per_night: 173, source: "Booking.com", verified_at: VERIFIED, free_cancellation: true },
  over_budget: false,
  reviews_summary:
    "23 reseñas leídas. Predominan comentarios positivos sobre el desayuno y el tamaño de los apartamentos. Varias mencionan que el edificio muestra algo de desgaste, sin quejas de limpieza.",
  reviews_analyzed: 23,
  location_summary: "Palermo Soho, sobre una avenida con buena conexión. Plaza Serrano a 9 min a pie; Plaza Italia a 12 min.",
  summary:
    "Cumple todo lo imprescindible y tres de las cuatro preferencias con evidencia. Un poco más caro que la opción 1 y con un edificio algo gastado, pero con pileta y cancelación gratis.",
  link: "https://www.google.com/travel/hotels/entity/ChoIm7bA2Nq0kMR7GgkvbS8wMV9wcXcQAQ",
  facts: facts({
    type: "Hotel",
    hotel_class: 4,
    overall_rating: 4.3,
    reviews_count: 1284,
    location_rating: 4.5,
    address: "Av. Córdoba 4700, Palermo, Buenos Aires",
    latitude: -34.5968,
    longitude: -58.4288,
    zone_inside: true,
    zone_distance_m: 0,
    essential_info: ["Pileta exterior", "Desayuno buffet"],
    amenities: ["Wifi", "Cocina", "Desayuno", "Pileta", "Gimnasio"],
    nearby_places: [
      { name: "Plaza Serrano", category: "Plaza", transportations: ["A pie 9 min"] },
      { name: "Estación Plaza Italia", category: "Subte", transportations: ["A pie 12 min"] },
    ],
    offers: [
      { source: "Booking.com", total: 519, per_night: 173, free_cancellation: true, link: "https://www.booking.com/hotel/ar/palermo-suites-buenos-aires.html" },
      { source: "Expedia", total: 519, per_night: 173, free_cancellation: false, link: "https://www.expedia.com/" },
      { source: "Hotels.com", total: 519, per_night: 173, free_cancellation: false, link: "https://www.hotels.com/" },
    ],
    booking_url: "https://www.booking.com/hotel/ar/palermo-suites-buenos-aires.html",
    booking_source: "Booking.com",
    google_hotels_url: "https://www.google.com/travel/search?q=Palermo%20Suites%20Buenos%20Aires%20Hotel%20%26%20Apartments",
    free_cancellation: true,
  }),
};

// Candidato de la web abierta: encontrado por web_search en el sitio de una inmobiliaria y leído con fetch_page.
// Sin reseñas de Google (no figura en Google Hotels); todo lo confirmado sale de la página.
const LOFT_URL = "https://palermoalquila.com.ar/temporario/loft-gurruchaga";
const loft: PropertyVerdict = {
  property_token: "web:4c1f9e2a7b30",
  name: "Loft Gurruchaga · Palermo Soho",
  fit_score: 66,
  recommend: true,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "la página lista wifi de fibra 300 Mb" },
    { constraint: "cocina o kitchenette", status: "confirmed", evidence: "la página describe cocina completa con horno y heladera" },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "unknown", evidence: null },
    { constraint: "cerca de Plaza Serrano", status: "confirmed", evidence: "Gurruchaga al 1700 según la página: 5 min a pie de Plaza Serrano" },
    { constraint: "terraza o pileta", status: "confirmed", evidence: "la página describe terraza privada con parrilla" },
    { constraint: "check-in flexible", status: "unknown", evidence: null },
  ],
  pros: ["Loft entero con cocina completa y terraza privada", "A 5 minutos a pie de Plaza Serrano", "Precio publicado por la inmobiliaria, sin comisión de portal"],
  cons: ["Sin reseñas públicas: no figura en Google Hotels ni en portales, así que no hay opiniones para contrastar"],
  red_flags: [],
  unknowns: ["Horario de check-in y si aceptan llegada tarde", "Política de cancelación", "Si el precio publicado incluye limpieza final"],
  price: { total: 510, currency: "USD", per_night: 170, source: "palermoalquila.com.ar", verified_at: VERIFIED, free_cancellation: null },
  over_budget: false,
  reviews_summary: "Sin reseñas de Google: el alojamiento no figura en Google Hotels y la inmobiliaria no publica opiniones. No se leyeron reseñas.",
  reviews_analyzed: 0,
  location_summary: "Palermo Soho, Gurruchaga al 1700 según la página. Plaza Serrano a 5 min a pie; estación Plaza Italia a 12 min.",
  summary:
    "Loft entero publicado en el sitio de una inmobiliaria de Palermo: cumple lo imprescindible y queda dentro del presupuesto, pero sin reseñas ni política de cancelación verificadas.",
  link: LOFT_URL,
  facts: facts({
    source: "web",
    url: LOFT_URL,
    booking_url: LOFT_URL, // candidato web: la página propia es el enlace exacto
    booking_source: "palermoalquila.com.ar",
    google_hotels_url: null, // no figura en Google Hotels
    type: "Loft",
    address: "Gurruchaga 1700, Palermo Soho, Buenos Aires",
    latitude: -34.5903,
    longitude: -58.4286,
    zone_inside: true,
    zone_distance_m: 0,
    essential_info: ["Loft entero", "Capacidad para 2", "Terraza privada"],
    amenities: ["Wifi", "Cocina completa", "Terraza", "Parrilla", "Aire acondicionado"],
    nearby_places: [
      { name: "Plaza Serrano", category: "Plaza", transportations: ["A pie 5 min"] },
      { name: "Estación Plaza Italia", category: "Subte", transportations: ["A pie 12 min"] },
    ],
    offers: [
      { source: "palermoalquila.com.ar", total: 510, per_night: 170, free_cancellation: null, link: LOFT_URL },
      // El mismo loft en un portal chico que solo publica precio por noche (sin total): se muestra "desde X / noche".
      { source: "alquilertemporario.com.ar", total: null, per_night: 178, free_cancellation: null, link: "https://alquilertemporario.com.ar/palermo/loft-gurruchaga" },
    ],
  }),
};

// Candidato de la web abierta con sitio propio (posada): reserva directa, sin Google Hotels.
const AROMOS_URL = "https://posadalosaromos.com.ar/habitaciones";
const aromos: PropertyVerdict = {
  property_token: "web:9b2d70e5a1c4",
  name: "Posada Los Aromos",
  fit_score: 58,
  recommend: true,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "la página lista wifi en todas las habitaciones" },
    { constraint: "cocina o kitchenette", status: "confirmed", evidence: "según la página, dos de las seis habitaciones tienen kitchenette" },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "confirmed", evidence: "la página indica desayuno casero incluido" },
    { constraint: "cerca de Plaza Serrano", status: "confirmed", evidence: "el mapa de la página la ubica a 14 min a pie de Plaza Serrano" },
    { constraint: "terraza o pileta", status: "confirmed", evidence: "la página describe terraza con reposeras" },
    { constraint: "check-in flexible", status: "unknown", evidence: null },
  ],
  pros: ["Desayuno casero incluido", "Reserva directa con la posada, sin intermediarios", "Terraza y patio interno"],
  cons: ["La kitchenette está solo en 2 de las 6 habitaciones: hay que pedirla al reservar", "Sin reseñas públicas para contrastar lo que dice la página"],
  red_flags: [],
  unknowns: ["Horario de check-in", "Política de cancelación"],
  price: { total: 480, currency: "USD", per_night: 160, source: "posadalosaromos.com.ar", verified_at: VERIFIED, free_cancellation: null },
  over_budget: false,
  reviews_summary: "Sin reseñas de Google: la posada no figura en Google Hotels. No se leyeron reseñas; lo confirmado sale de su propio sitio.",
  reviews_analyzed: 0,
  location_summary: "Palermo Viejo, sobre una calle arbolada según la página. Plaza Serrano a 14 min a pie; estación Scalabrini Ortiz a 9 min.",
  summary:
    "Posada con sitio propio y desayuno incluido, dentro del presupuesto. Cumple lo imprescindible solo en la categoría con kitchenette y no hay reseñas públicas que respalden lo que promete la página.",
  link: AROMOS_URL,
  facts: facts({
    source: "web",
    url: AROMOS_URL,
    booking_url: AROMOS_URL,
    booking_source: "posadalosaromos.com.ar",
    google_hotels_url: null,
    type: "Posada",
    address: "Malabia 1500, Palermo Viejo, Buenos Aires",
    essential_info: ["6 habitaciones", "Desayuno casero"],
    amenities: ["Wifi", "Kitchenette (2 habitaciones)", "Desayuno", "Terraza", "Patio"],
    nearby_places: [
      { name: "Plaza Serrano", category: "Plaza", transportations: ["A pie 14 min"] },
      { name: "Estación Scalabrini Ortiz", category: "Subte", transportations: ["A pie 9 min"] },
    ],
    offers: [{ source: "posadalosaromos.com.ar", total: 480, per_night: 160, free_cancellation: null, link: "https://posadalosaromos.com.ar/reservas" }],
  }),
};

const mine: PropertyVerdict = {
  property_token: "ChkI7cfl1aWqz7VfGg0vZy8xMXJ4bDNqcXIQAQ",
  name: "Mine Hotel Boutique",
  fit_score: 48,
  recommend: false,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "amenities de la ficha" },
    { constraint: "cocina o kitchenette", status: "unknown", evidence: null },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "confirmed", evidence: "ficha: desayuno incluido" },
    { constraint: "cerca de Plaza Serrano", status: "unknown", evidence: null },
    { constraint: "terraza o pileta", status: "unknown", evidence: null },
    { constraint: "check-in flexible", status: "unknown", evidence: null },
  ],
  pros: ["Reseñas consistentes sobre el desayuno y el patio interno"],
  cons: ["Sin tarifa disponible para estas fechas en las fuentes consultadas"],
  red_flags: [],
  unknowns: ["Precio para las fechas pedidas", "Si hay kitchenette en alguna categoría", "Distancia a Plaza Serrano (la consulta de detalles falló por timeout)", "Si la pileta del patio está habilitada"],
  price: null,
  over_budget: null,
  reviews_summary: "18 reseñas leídas, mayormente positivas sobre desayuno, patio y silencio. Sin quejas recurrentes.",
  reviews_analyzed: 18,
  location_summary: "Palermo Soho según la ficha de búsqueda; no se pudo obtener el detalle de lugares cercanos.",
  summary: "Boutique con buena reputación, pero con demasiado sin verificar: la consulta de detalles dio timeout y no hay precio para las fechas. Sin precio ni evidencia de cocina, queda fuera.",
  link: "https://www.google.com/travel/hotels/entity/ChkI7cfl1aWqz7VfGg0vZy8xMXJ4bDNqcXIQAQ",
  // Sin ofertas (la consulta de detalles dio timeout): no hay booking_url, solo la búsqueda en Google Hotels.
  facts: facts({ type: "Hotel boutique", overall_rating: 4.5, reviews_count: 420, google_hotels_url: "https://www.google.com/travel/search?q=Mine%20Hotel%20Boutique%20Buenos%20Aires" }),
};

const vain: PropertyVerdict = {
  property_token: "ChkIvcKb5MLe2Zx6Gg0vZy8xMXJ4bDNqcXIQAQ",
  name: "Vain Boutique Hotel",
  fit_score: 31,
  recommend: false,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "amenities de la ficha" },
    { constraint: "cocina o kitchenette", status: "unknown", evidence: null },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "confirmed", evidence: "ficha: desayuno incluido" },
    { constraint: "cerca de Plaza Serrano", status: "confirmed", evidence: "Plaza Serrano a 6 min a pie" },
    { constraint: "terraza o pileta", status: "unknown", evidence: null },
    { constraint: "check-in flexible", status: "unknown", evidence: null },
  ],
  pros: ["Ubicación a 6 min de Plaza Serrano"],
  cons: ["Varias reseñas de 2026 mencionan habitaciones sin renovar"],
  red_flags: ["Cinco reseñas de los últimos tres meses reportan obras ruidosas en el edificio lindero durante el día"],
  unknowns: ["Si hay kitchenette", "Si la terraza está abierta a huéspedes"],
  price: { total: 402, currency: "USD", per_night: 134, source: "vio.com", verified_at: VERIFIED, free_cancellation: null },
  over_budget: false,
  reviews_summary: "22 reseñas leídas. Buenas opiniones sobre ubicación y desayuno, pero una queja recurrente y reciente sobre ruido de obra durante el día.",
  reviews_analyzed: 22,
  location_summary: "Palermo Soho, a 6 min a pie de Plaza Serrano.",
  summary: "Barato y bien ubicado, pero con una señal de alerta seria: obra ruidosa al lado reportada de forma recurrente en reseñas recientes.",
  // Google Hotels solo devolvió un comparador (vio.com): el backend no lo elige como booking_url y la UI ofrece "Buscar en Google Hotels".
  link: "https://www.vio.com/hotel/vain-boutique-hotel-buenos-aires",
  facts: facts({
    type: "Hotel boutique",
    overall_rating: 4.2,
    reviews_count: 510,
    location_rating: 4.6,
    address: "Thames 2226, Palermo Soho, Buenos Aires",
    essential_info: ["Desayuno incluido"],
    nearby_places: [{ name: "Plaza Serrano", category: "Plaza", transportations: ["A pie 6 min"] }],
    offers: [{ source: "vio.com", total: 402, per_night: 134, free_cancellation: null, link: "https://www.vio.com/hotel/vain-boutique-hotel-buenos-aires" }],
    booking_url: null,
    booking_source: null,
    google_hotels_url: "https://www.google.com/travel/search?q=Vain%20Boutique%20Hotel%20Buenos%20Aires",
  }),
};

const own: PropertyVerdict = {
  property_token: "ChkIw8-lp9jV9Y5rGg0vZy8xMXJ4bDNqcXIQAQ",
  name: "Own Palermo Hollywood",
  fit_score: 20,
  recommend: false,
  hard_constraints: [
    { constraint: "wifi", status: "confirmed", evidence: "amenities de la ficha" },
    { constraint: "cocina o kitchenette", status: "violated", evidence: "la ficha lista 'sin cocina' entre las amenities excluidas" },
  ],
  soft_preferences: [
    { constraint: "desayuno incluido", status: "confirmed", evidence: "ficha: desayuno incluido" },
    { constraint: "cerca de Plaza Serrano", status: "violated", evidence: "Plaza Serrano a 20 min a pie" },
    { constraint: "terraza o pileta", status: "confirmed", evidence: "amenities: terraza" },
    { constraint: "check-in flexible", status: "unknown", evidence: null },
  ],
  pros: ["Terraza con vista, bien valorada"],
  cons: [],
  red_flags: [],
  unknowns: ["Horario de check-in"],
  price: { total: 441, currency: "USD", per_night: 147, source: "Booking.com", verified_at: VERIFIED, free_cancellation: true },
  over_budget: false,
  reviews_summary: "19 reseñas leídas, en general positivas sobre la terraza y el staff.",
  reviews_analyzed: 19,
  location_summary: "Palermo Hollywood, cerca de Av. Juan B. Justo. Plaza Serrano a 20 min a pie.",
  summary: "Buen hotel, pero no tiene cocina en ninguna categoría, que era condición imprescindible. Queda fuera del ranking.",
  link: "https://www.google.com/travel/hotels/entity/ChkIw8-lp9jV9Y5rGg0vZy8xMXJ4bDNqcXIQAQ",
  facts: facts({
    type: "Hotel",
    hotel_class: 3,
    overall_rating: 4.3,
    reviews_count: 880,
    address: "Cabrera 5556, Palermo Hollywood, Buenos Aires",
    essential_info: ["Terraza con vista"],
    excluded_amenities: ["Cocina"],
    offers: [{ source: "Booking.com", total: 441, per_night: 147, free_cancellation: true, link: "https://www.booking.com/hotel/ar/own-palermo-hollywood.html" }],
    booking_url: "https://www.booking.com/hotel/ar/own-palermo-hollywood.html",
    booking_source: "Booking.com",
    google_hotels_url: "https://www.google.com/travel/search?q=Own%20Palermo%20Hollywood",
  }),
};

export const sampleReport: FinalReport = {
  run_id: "20260911-184200-ab12",
  generated_at: "2026-09-11T21:44:32+00:00",
  language: "es",
  brief: {
    destination: "Buenos Aires, Argentina",
    area_preferences: ["Palermo", "Palermo Soho"],
    check_in: "2026-10-16",
    check_out: "2026-10-19",
    adults: 2,
    children: 0,
    children_ages: [],
    currency: "USD",
    budget_total_max: 600,
    budget_per_night_max: null,
    property_types: ["hotel", "apart-hotel"],
    dealbreakers: ["hostel", "baño compartido"],
    hard_constraints: ["wifi", "cocina o kitchenette"],
    soft_preferences: ["desayuno incluido", "cerca de Plaza Serrano", "terraza o pileta", "check-in flexible"],
    travel_purpose: "escapada de fin de semana largo",
    notes: "Llegamos tarde el jueves.",
    language: "es",
    gl: "ar",
    zone: PALERMO_ZONE,
    nights: 3,
  },
  summary:
    "Para 3 noches en Palermo con USD 600 de tope, la opción más sólida es Nido @ Palermo Soho Square: cumple las dos condiciones imprescindibles, las cuatro preferencias y queda 130 dólares por debajo del presupuesto. Palermo Suites es la alternativa con pileta y cancelación gratis, algo más cara. Fuera de Google Hotels aparecieron dos opciones en la web abierta: un loft entero en el sitio de una inmobiliaria y una posada con reserva directa; ambas cumplen lo imprescindible pero sin reseñas públicas. Quedaron fuera dos hostels por el dealbreaker, un hotel de Recoleta por zona, uno sin precio para las fechas y uno con obra ruidosa al lado.",
  ranking: [
    {
      rank: 1,
      rationale:
        "Es la única con las cuatro preferencias confirmadas con evidencia y a la vez la más barata de las que cumplen ambas condiciones imprescindibles. El ruido de fin de semana es el único punto en contra y aplica a casi todo Palermo Soho.",
      verdict: nido,
    },
    {
      rank: 2,
      rationale:
        "Cumple lo imprescindible con pileta y desayuno confirmados, y tiene cancelación gratis. Queda segunda por precio (USD 51 más) y porque no se pudo verificar el check-in tardío que necesitan el jueves.",
      verdict: palermoSuites,
    },
    {
      rank: 3,
      rationale:
        "Loft entero a 5 minutos de Plaza Serrano, encontrado en el sitio de una inmobiliaria (no está en Google Hotels). Cumple lo imprescindible dentro del presupuesto; queda tercero porque no hay reseñas públicas ni política de cancelación verificada.",
      verdict: loft,
    },
    {
      rank: 4,
      rationale:
        "Posada con sitio propio, desayuno incluido y precio por debajo del tope. Pierde puntos porque la kitchenette está solo en dos habitaciones y no hay reseñas que respalden lo que dice la página.",
      verdict: aromos,
    },
  ],
  verdicts: [nido, palermoSuites, loft, aromos, mine, vain, own],
  discarded: [
    { property_token: "ChkIq6rM6b3f7pZlGg0vZy8xMXJ4bDNqcXIQAQ", name: "Play Hostel Soho", reason: "Tipo 'hostel'; 'hostel' está entre los dealbreakers.", stage: "discovery" },
    { property_token: "ChkI9pCq2q7z5b1CGg0vZy8xMXJ4bDNqcXIQAQ", name: "Milhouse Hostel Hipo", reason: "Tipo 'hostel' con habitaciones compartidas; dos dealbreakers.", stage: "discovery" },
    { property_token: "ChkI5oyU-oKmyNhtGg0vZy8xMXJ4bDNqcXIQAQ", name: "Hotel Bel Air", reason: "Está en Recoleta, fuera de las zonas pedidas (Palermo).", stage: "orchestrator" },
    { property_token: "ChkIzq7f3Yy2h8dZGg0vZy8xMXJ4bDNqcXIQAQ", name: "Dazzler by Wyndham Recoleta", reason: "Fuera de Palermo: a 1200 m del borde", stage: "discovery" },
    { property_token: "ChkI4tX0nJqB2M1jGg0vZy8xMXJ4bDNqcXIQAQ", name: "Hotel Colón Villa Crespo", reason: "Fuera de Palermo: a 1200 m del borde", stage: "discovery" },
    { property_token: own.property_token, name: own.name, reason: "Condición imprescindible 'cocina o kitchenette' violada según la ficha.", stage: "analyst" },
    { property_token: vain.property_token, name: vain.name, reason: "Señal de alerta: obra ruidosa al lado, reportada de forma recurrente en reseñas recientes.", stage: "analyst" },
  ],
  caveats: [
    "La consulta de detalles de Mine Hotel Boutique dio timeout: su distancia a Plaza Serrano y la existencia de kitchenette quedaron sin verificar.",
    "Los precios corresponden al momento de la búsqueda y pueden cambiar; verificá en la fuente antes de reservar.",
    "No se verificó la política de cancelación de la tarifa de Nido (eDreams no la informa).",
    "Loft Gurruchaga y Posada Los Aromos salieron de la web abierta: sus datos vienen de la página de cada uno y no hay reseñas de Google para contrastarlos.",
  ],
  stats: {
    candidates_found: 29,
    shortlisted: 8,
    analyzed: 7,
    serpapi_calls: 19,
    tool_errors: 1,
    duration_seconds: 142.6,
    timed_out: false,
    orchestrator_model: "claude-sonnet-4-5",
    analyst_model: "claude-haiku-4-5",
  },
};

/** Un paso del guion: un mensaje del usuario o un evento del agente. Los `ts` se reescriben al reproducir. */
export type MockStep = { user: string } | RunEvent;

export const sampleThreadSummary: ThreadSummary = {
  thread_id: "mock",
  run_id: sampleReport.run_id,
  status: "idle",
  created_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
  destination: sampleReport.brief.destination,
  check_in: sampleReport.brief.check_in,
  check_out: sampleReport.brief.check_out,
  has_report: true,
  fit_top: sampleReport.ranking[0].verdict.fit_score,
  results_count: sampleReport.ranking.length,
  prices_stale: false,
  last_message: "Listo: cuatro opciones que cierran. La mejor es Nido @ Palermo Soho Square.",
};

/** Mis búsquedas en modo demostración: una corriendo, una con resultados y una con precios vencidos. */
export const sampleSearches: ThreadSummary[] = [
  {
    thread_id: "mock-running",
    run_id: "20260912-101500-cc31",
    status: "running",
    created_at: new Date(Date.now() - 2 * 60_000).toISOString(),
    destination: "Cabo Polonio, Uruguay",
    check_in: "2027-02-14",
    check_out: "2027-02-17",
    has_report: false,
    fit_top: null,
    results_count: null,
    prices_stale: false,
    last_message: "Arranco. Primero busco candidatos en Cabo Polonio.",
  },
  sampleThreadSummary,
  {
    thread_id: "mock-stale",
    run_id: "20260820-093000-9f0e",
    status: "idle",
    created_at: new Date(Date.now() - 21 * 86_400_000).toISOString(),
    destination: "Montevideo, Uruguay",
    check_in: "2026-11-03",
    check_out: "2026-11-06",
    has_report: true,
    fit_top: 71,
    results_count: 3,
    prices_stale: true,
    last_message: "Listo. Tres opciones en Pocitos y Punta Carretas.",
  },
];

/** Detalle mínimo por hilo para la segunda línea de la lista (viajeros · imprescindibles). */
export const sampleSearchDetails: Record<string, ThreadDetail> = {
  mock: {
    ...sampleThreadSummary,
    messages: [],
    draft: { ...emptyDraft(), destination: sampleReport.brief.destination },
    brief: sampleReport.brief,
    confirmed: true,
    report: sampleReport,
    error: null,
    events_count: 0,
  },
  "mock-running": {
    ...sampleSearches[0],
    messages: [],
    draft: { ...emptyDraft(), destination: "Cabo Polonio, Uruguay", adults: 2, hard_constraints: ["sin auto"] },
    brief: { destination: "Cabo Polonio, Uruguay", check_in: "2027-02-14", check_out: "2027-02-17", adults: 2, hard_constraints: ["sin auto"] },
    confirmed: true,
    report: null,
    error: null,
    events_count: 0,
  },
  "mock-stale": {
    ...sampleSearches[2],
    messages: [],
    draft: { ...emptyDraft(), destination: "Montevideo, Uruguay", adults: 1, travel_purpose: "trabajo" },
    brief: { destination: "Montevideo, Uruguay", check_in: "2026-11-03", check_out: "2026-11-06", adults: 1, area_preferences: ["Pocitos"], hard_constraints: ["wifi", "escritorio"] },
    confirmed: true,
    report: null,
    error: null,
    events_count: 0,
  },
};

/** Eventos de la investigación (entre run_started y run_finished, exclusivos). */
function researchEvents(report: FinalReport, ts: () => string): RunEvent[] {
  const run_id = report.run_id;
  const model = (agent: RunEvent["agent"], m: string, seconds: number, inp: number, out: number): RunEvent => ({
    type: "model",
    run_id,
    ts: ts(),
    agent,
    message: `${m} · ${seconds.toFixed(1)} s`,
    data: { model: m, seconds, input_tokens: inp, output_tokens: out },
  });
  const ev: RunEvent[] = [
    { type: "phase", run_id, ts: ts(), agent: "orchestrator", message: "Buscando candidatos en Palermo", data: { phase: "discovery" } },
    { type: "subagent_started", run_id, ts: ts(), agent: "orchestrator", message: "Recorriendo los portales", data: { subagent: "hotel-discovery", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Buscando hoteles en Palermo, Buenos Aires", data: { tool: "search_hotels", args: { q: "Palermo, Buenos Aires", check_in: "2026-10-16", check_out: "2026-10-19", adults: 2 }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "18 candidatos", data: { tool: "search_hotels", ok: true, summary: "18 candidatos, 4 con precio total; rango USD 210–1.120", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Buscando apart-hoteles en Palermo Soho", data: { tool: "search_hotels", args: { q: "apart hotel Palermo Soho", check_in: "2026-10-16", check_out: "2026-10-19", adults: 2 }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "9 candidatos nuevos", data: { tool: "search_hotels", ok: true, summary: "12 resultados, 9 nuevos; 2 hostels descartados por dealbreaker", agent_id: "disc-1" } },
    // Web abierta: búsqueda → lectura de páginas → candidatos (nuevos o unificados con uno de Google Hotels).
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Buscando en la web: alquiler temporario en Palermo Soho", data: { tool: "web_search", args: { query: "alquiler temporario Palermo Soho loft terraza 3 noches" }, agent_id: "disc-1" } },
    {
      type: "tool_result",
      run_id,
      ts: ts(),
      agent: "hotel-discovery",
      message: "6 resultados",
      data: {
        tool: "web_search",
        ok: true,
        summary: "6 resultados: 2 inmobiliarias, 1 posada, 3 portales que bloquean lectura",
        results: [
          { title: "Loft Gurruchaga – Alquiler temporario en Palermo Soho", url: LOFT_URL },
          { title: "Departamentos temporarios en Palermo | Palermo Alquila", url: "https://palermoalquila.com.ar/temporario" },
          { title: "Posada Los Aromos – Palermo Viejo, Buenos Aires", url: AROMOS_URL },
          { title: "Nido Palermo Soho – Reservas", url: "https://nidopalermo.com.ar/reservas" },
          { title: "Alquileres temporarios en Palermo – Airbnb", url: "https://www.airbnb.com/s/Palermo--Buenos-Aires" },
          { title: "Los 10 mejores apartamentos en Palermo – Booking.com", url: "https://www.booking.com/apartments/city/ar/buenos-aires.html" },
        ],
        agent_id: "disc-1",
      },
    },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Leyendo palermoalquila.com.ar", data: { tool: "fetch_page", args: { url: LOFT_URL }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "Página leída", data: { tool: "fetch_page", ok: true, summary: "Loft Gurruchaga – Alquiler temporario en Palermo Soho (3.412 chars)", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Sumando candidato web", data: { tool: "add_web_candidate", args: { name: "Loft Gurruchaga · Palermo Soho", url: LOFT_URL, total: 510, currency: "USD" }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "Candidato web agregado", data: { tool: "add_web_candidate", ok: true, summary: "Loft Gurruchaga · Palermo Soho vía palermoalquila.com.ar", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Leyendo posadalosaromos.com.ar", data: { tool: "fetch_page", args: { url: AROMOS_URL }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "Página leída", data: { tool: "fetch_page", ok: true, summary: "Posada Los Aromos – Habitaciones y tarifas (2.870 chars)", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Sumando candidato web", data: { tool: "add_web_candidate", args: { name: "Posada Los Aromos", url: AROMOS_URL, per_night: 160, currency: "USD" }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "Candidato web agregado", data: { tool: "add_web_candidate", ok: true, summary: "Posada Los Aromos vía posadalosaromos.com.ar", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Leyendo nidopalermo.com.ar", data: { tool: "fetch_page", args: { url: "https://nidopalermo.com.ar/reservas" }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "Página leída", data: { tool: "fetch_page", ok: true, summary: "Nido Palermo Soho – Reservas (1.960 chars)", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Sumando candidato web", data: { tool: "add_web_candidate", args: { name: "Nido Palermo Soho", url: "https://nidopalermo.com.ar/reservas", total: 480, currency: "USD" }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "Unificado con un candidato de Google Hotels", data: { tool: "add_web_candidate", ok: true, summary: "unificado con Nido @ Palermo Soho Square", agent_id: "disc-1" } },
    { type: "tool_call", run_id, ts: ts(), agent: "hotel-discovery", message: "Leyendo airbnb.com", data: { tool: "fetch_page", args: { url: "https://www.airbnb.com/s/Palermo--Buenos-Aires" }, agent_id: "disc-1" } },
    { type: "tool_result", run_id, ts: ts(), agent: "hotel-discovery", message: "airbnb.com bloquea la lectura automatizada", data: { tool: "fetch_page", ok: false, summary: "dominio bloqueado", error: "airbnb.com bloquea la lectura automatizada; sus propiedades llegan por Google Hotels", agent_id: "disc-1" } },
    model("hotel-discovery", "claude-sonnet-4-5", 21.7, 18400, 1420),
    { type: "subagent_finished", run_id, ts: ts(), agent: "orchestrator", message: "Descartados 21 por precio, zona o dealbreakers: 8 pasan tus filtros (2 de la web)", data: { subagent: "hotel-discovery", agent_id: "disc-1", shortlist: 8 } },
    { type: "phase", run_id, ts: ts(), agent: "orchestrator", message: "Eligiendo cuáles mirar a fondo", data: { phase: "selection" } },
    { type: "message", run_id, ts: ts(), agent: "orchestrator", message: "Hotel Bel Air descartado: está en Recoleta, fuera de las zonas pedidas", data: {} },
    model("orchestrator", "claude-sonnet-4-5", 4.2, 3980, 420),
    { type: "phase", run_id, ts: ts(), agent: "orchestrator", message: "Leyendo reseñas y comparando precios de 7 alojamientos", data: { phase: "analysis" } },
  ];
  const analysts = report.verdicts;
  analysts.forEach((v, n) => {
    ev.push({ type: "subagent_started", run_id, ts: ts(), agent: "orchestrator", message: `Analizando ${v.name}`, data: { subagent: "property-analyst", agent_id: `an-${n + 1}`, property_name: v.name } });
  });
  analysts.forEach((v, n) => {
    const id = `an-${n + 1}`;
    if (v.facts?.source === "web") {
      const url = v.facts.url ?? v.link ?? "";
      ev.push({ type: "tool_call", run_id, ts: ts(), agent: "property-analyst", message: `Leyendo la página de ${v.name}`, data: { tool: "fetch_page", args: { url }, agent_id: id } });
      ev.push({ type: "tool_result", run_id, ts: ts(), agent: "property-analyst", message: "Página leída", data: { tool: "fetch_page", ok: true, summary: `${v.name} (${2400 + n * 310} chars)`, property_name: v.name, agent_id: id } });
      ev.push(model("property-analyst", "claude-haiku-4-5", 7.2 + n, 5100 + n * 90, 720));
      ev.push({ type: "verdict", run_id, ts: ts(), agent: "property-analyst", message: `Veredicto: ${v.name} — ${v.fit_score}/100`, data: { verdict: v } });
      ev.push({ type: "subagent_finished", run_id, ts: ts(), agent: "orchestrator", message: `Listo: ${v.name}`, data: { subagent: "property-analyst", agent_id: id, property_name: v.name } });
      return;
    }
    ev.push({ type: "tool_call", run_id, ts: ts(), agent: "property-analyst", message: `Comparando precios de ${v.name} entre fuentes`, data: { tool: "get_property_details", args: { property_token: v.property_token }, agent_id: id } });
    if (v.name === "Mine Hotel Boutique") {
      ev.push({ type: "tool_result", run_id, ts: ts(), agent: "property-analyst", message: "Timeout de SerpAPI", data: { tool: "get_property_details", ok: false, summary: "timeout tras 20 s", error: "timeout", property_name: v.name, agent_id: id } });
      ev.push({ type: "warning", run_id, ts: ts(), agent: "property-analyst", message: "Detalles de Mine Hotel Boutique no disponibles: los campos afectados quedan sin verificar", data: { error: "timeout", retry: false } });
    } else {
      ev.push({ type: "tool_result", run_id, ts: ts(), agent: "property-analyst", message: "Ficha obtenida", data: { tool: "get_property_details", ok: true, summary: `${v.price ? `mejor oferta ${v.price.currency} ${v.price.total} vía ${v.price.source}` : "sin ofertas"}; ${8 + n} amenities; ${v.facts?.offers.length ?? 0} fuentes`, property_name: v.name, agent_id: id } });
    }
    ev.push({ type: "tool_call", run_id, ts: ts(), agent: "property-analyst", message: `Leyendo reseñas de ${v.name}`, data: { tool: "get_property_reviews", args: { property_token: v.property_token, sort_by: "newest" }, agent_id: id } });
    ev.push({ type: "tool_result", run_id, ts: ts(), agent: "property-analyst", message: `${v.reviews_analyzed} reseñas`, data: { tool: "get_property_reviews", ok: true, summary: `${v.reviews_analyzed} reseñas recientes, puntaje promedio 4,${3 + (n % 5)}`, property_name: v.name, agent_id: id } });
    ev.push(model("property-analyst", "claude-haiku-4-5", 9.8 + n, 7400 + n * 120, 900));
    ev.push({ type: "verdict", run_id, ts: ts(), agent: "property-analyst", message: `Veredicto: ${v.name} — ${v.fit_score}/100`, data: { verdict: v } });
    ev.push({ type: "subagent_finished", run_id, ts: ts(), agent: "orchestrator", message: `Listo: ${v.name}`, data: { subagent: "property-analyst", agent_id: id, property_name: v.name } });
  });
  ev.push({ type: "phase", run_id, ts: ts(), agent: "orchestrator", message: "Armando el ranking", data: { phase: "consolidation" } });
  ev.push(model("orchestrator", "claude-sonnet-4-5", 11.3, 14200, 1680));
  return ev;
}

/**
 * Conversación guionada: dos turnos que arman el brief (con respuestas rápidas), la confirmación y, en el mismo turno,
 * la búsqueda completa hasta el mensaje final del agente.
 */
export function sampleConversation(report: FinalReport): MockStep[] {
  const run_id = report.run_id;
  const t0 = Date.parse("2026-09-11T21:38:02+00:00");
  let i = 0;
  const ts = () => new Date(t0 + i++ * 4200).toISOString();
  const brief = report.brief;

  const assistant = (message: string): RunEvent => ({ type: "assistant", run_id, ts: ts(), agent: "orchestrator", message, data: { role: "assistant" } });
  const suggest = (options: string[]): RunEvent => ({ type: "suggestions", run_id, ts: ts(), agent: "orchestrator", message: options.join(" · "), data: { options } });
  const turnDone = (): RunEvent => ({ type: "turn_done", run_id, ts: ts(), agent: "system", message: "Turno terminado", data: { status: "ok" } });

  return [
    { user: "Quiero 3 noches en Palermo en octubre, con pileta" },
    {
      type: "brief_saved",
      run_id,
      ts: ts(),
      agent: "orchestrator",
      message: "Brief guardado (incompleto)",
      data: {
        status: "incomplete",
        missing: ["check_in", "check_out", "adults"],
        draft: { ...emptyDraft(), destination: "Buenos Aires, Argentina", area_preferences: ["Palermo"], soft_preferences: ["pileta"], language: "es", gl: "ar" },
        confirmed: false,
      },
    },
    { type: "model", run_id, ts: ts(), agent: "orchestrator", message: "claude-sonnet-4-5 · 3.1 s", data: { model: "claude-sonnet-4-5", seconds: 3.1, input_tokens: 2210, output_tokens: 180 } },
    suggest(["Somos 2 adultos", "2 adultos + 2 chicos", "Todavía no sé las fechas"]),
    assistant("Anoté Palermo, en Buenos Aires, 3 noches en octubre y pileta como preferencia. Para buscar necesito las fechas exactas. ¿Cuántos viajan, y tienen un tope de presupuesto?"),
    turnDone(),

    {
      user: "Del 16 al 19 de octubre, dos adultos, hasta 600 dólares en total. Necesitamos wifi y cocina; nada de hostels ni baño compartido. Si tiene desayuno y queda cerca de Plaza Serrano, mejor. Llegamos tarde el jueves.",
    },
    { type: "brief_saved", run_id, ts: ts(), agent: "orchestrator", message: "Brief guardado", data: { status: "ready", missing: [], brief, confirmed: false } },
    { type: "model", run_id, ts: ts(), agent: "orchestrator", message: "claude-sonnet-4-5 · 4.6 s", data: { model: "claude-sonnet-4-5", seconds: 4.6, input_tokens: 2890, output_tokens: 310 } },
    suggest(["Sí, dale", "Quiero cambiar algo"]),
    assistant(
      "Queda así: Buenos Aires, en Palermo o Palermo Soho, del jueves 16 al domingo 19 de octubre (3 noches), 2 adultos, tope USD 600 en total. Imprescindibles: wifi y cocina o kitchenette. Descarto hostels y baño compartido. Suman puntos: desayuno incluido, cerca de Plaza Serrano, terraza o pileta y check-in flexible por la llegada tarde. ¿Arranco la búsqueda con esto?",
    ),
    turnDone(),

    { user: "Sí, dale." },
    { type: "brief_saved", run_id, ts: ts(), agent: "orchestrator", message: "Brief confirmado", data: { status: "ready", missing: [], brief, confirmed: true } },
    { type: "run_started", run_id, ts: ts(), agent: "system", message: "Búsqueda iniciada", data: { brief } },
    assistant("Arranco. Primero busco candidatos en Palermo; después miro cada uno a fondo: precio en cada fuente, reseñas y ubicación. Tarda unos minutos."),
    ...researchEvents(report, ts),
    {
      type: "run_finished",
      run_id,
      ts: ts(),
      agent: "system",
      message: "Reporte listo",
      data: { report, assertions: { ok: true, checks: { hard_constraints: true, prices_traceable: true, no_verbatim_reviews: true, language: true, caps: true } } },
    },
    suggest(["Contame más de Nido", "Buscá más baratas"]),
    assistant(
      "Listo: cuatro opciones que cierran. La mejor es Nido @ Palermo Soho Square: cumple todo lo imprescindible y las cuatro preferencias, USD 468 en total vía eDreams. Si prefieren pileta y cancelación gratis, Palermo Suites es la alternativa por USD 51 más. Fuera de Google Hotels encontré un loft entero en el sitio de una inmobiliaria y una posada con reserva directa: entran en presupuesto, pero sin reseñas públicas. ¿Querés que profundice en alguna?",
    ),
    turnDone(),
  ];
}
