# CLAUDE.md — lodging-agent

Deep research de alojamientos: dado un brief de viaje (JSON), un deep agent (deepagents/LangGraph) investiga con SerpAPI Google Hotels y devuelve un top 5 justificado con evidencia real de precios, reviews y ubicación, en 1–3 minutos.

Documento de referencia: `docs/diseño.md` (diseño completo del agente). Ante conflicto entre código y diseño, preguntar antes de resolver por tu cuenta.

## Comandos

```bash
uv sync                                          # instalar deps
uv run pytest                                    # tests (nunca tocan la red)
uv run python -m lodging.run --brief <path>      # run e2e por CLI
uv run python -m lodging.evals                   # suite de evals sobre goldens
uv run uvicorn lodging.api.main:app --reload     # backend dev
```

Variables de entorno en `.env` (ver `.env.example`): `SERPAPI_KEY`, `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`.

## Arquitectura (resumen)

Dos fases separadas a propósito:

1. **Chat de brief** (`src/lodging/brief/`): LLM liviano + structured output → produce un `Brief`. No usa deepagents.
2. **Deep agent** (`src/lodging/agents/`): orquestador (sonnet) que delega en `hotel-discovery` (haiku) y hasta 8 `property-analyst` (haiku) **en paralelo**, y consolida en `FinalReport`.

Principio rector: **el orquestador nunca ve datos crudos.** Los subagentes absorben el ruido (JSONs de SerpAPI, reviews) y devuelven resultados compactos. Discovery deposita candidatos en `/candidates/hotels.json` vía el filesystem virtual de deepagents; el orquestador lee de ahí.

```
src/lodging/
├── config.py        # caps y modelos — ÚNICA fuente de verdad de constantes
├── schemas.py       # Brief, Candidate, PropertyVerdict, FinalReport (contratos)
├── tools/           # capa SerpAPI: cliente + las 3 tools
├── agents/          # prompts, subagentes, orquestador
├── brief/           # chat de brief (fase 1)
├── api/             # FastAPI + SSE
└── evals/           # goldens + assertions
```

## Reglas de dominio — NO negociables

Estas reglas son decisiones de diseño deliberadas. No las "mejores" ni las relajes al refactorizar. Si un cambio las toca, frenar y preguntar.

1. **Las tools normalizan y recortan.** Jamás devolver JSON crudo de SerpAPI al modelo. Cada tool retorna solo los campos del schema definido en `schemas.py`.
2. **Descartar solo con evidencia positiva.** En discovery y en el filtro del orquestador, un candidato se descarta únicamente ante evidencia positiva de violación (ej. `type=hostel` con "hostel" como dealbreaker). La **ausencia** de una amenity en datos de búsqueda NUNCA es motivo de descarte — eso lo verifica el analista.
3. **Ausencia de evidencia = `unknown`.** Nunca un pro, nunca un con, nunca inventado. Aplica a prompts, schemas y código.
4. **fit_score por rúbrica, no por vibra.** La rúbrica vive en `schemas.py` (base 50; +8 por soft pref confirmada, tope +40; −10 por con relevante; −25 por red flag; −15 over budget; hard violada → ≤20 y `recommend=false`; clip 0–100). Prompt del analista y schema deben citar la misma rúbrica. Si se cambia, se cambia en un solo lugar y se re-corren los evals.
5. **Precios: siempre el total final con impuestos**, con `price_verified_at` (ISO) y `price_source`. En UI/reporte se presenta como "desde X vía <fuente>, verificado a las HH:MM". Nunca mostrar un precio sin origen trazable a un tool output.
6. **Reviews: parafrasear siempre.** Internamente se lee texto completo (truncado a 400 chars); hacia el usuario jamás se reproduce texto de review verbatim — se agrega y parafrasea ("6 reviews recientes mencionan wifi rápido"). Hay una assertion que lo verifica.
7. **Degradación parcial, no muerte del run.** Errores/timeouts de SerpAPI devuelven un objeto de error estructurado; el analista refleja el campo afectado como `unknown` y sigue. El timeout global (240 s) es red de seguridad última, no mecanismo de consolidación.
8. **Concurrencia SerpAPI acotada.** Todas las llamadas pasan por el semáforo global (`SERPAPI_CONCURRENCY=5` en `config.py`). No crear clientes HTTP paralelos que lo salteen.
9. **Caps duros en `config.py`**: `MAX_CANDIDATES=30`, `MAX_ANALYSTS=8`, `MAX_REVIEWS_PER_CALL=25`, `REVIEW_TEXT_MAX_CHARS=400`. Los prompts los repiten, pero la verdad vive en config. No hardcodear números sueltos.
10. **Geometría es código, no LLM** (feature de zonas, v2): point-in-polygon, distancias y buffers se computan con Shapely en la capa de tools. El modelo solo narra resultados ya calculados. Nunca pedirle a un LLM que razone sobre coordenadas.
11. **El orquestador no tiene tools de datos.** Solo `write_todos`, `task` y filesystem. Si puede llamar a SerpAPI directo, el diseño está roto.
12. **Nunca inventar** propiedades, precios, links ni contenido de reviews. Todo dato del reporte debe ser trazable a un tool output de la misma corrida (las assertions lo chequean).

## Convenciones de código

- Python 3.12, `async` en toda la capa de I/O. Type hints en todo; Pydantic v2 para contratos.
- Los docstrings de las tools son **parte del prompt** — escribirlos para el modelo (qué hace, qué devuelve, cuándo usarla), no para Sphinx.
- Prompts de agentes en **inglés** (rinden mejor); strings de cara al usuario en `brief.language`. Código y comentarios en español está bien.
- Cambios en `schemas.py` son cambios de contrato: actualizar a la vez prompts, assertions y (si aplica) frontend. Buscar todos los usos antes de tocar.
- Errores hacia el modelo: JSON estructurado. Errores hacia el dev: excepciones + logging estructurado.

## Testing y evals

- `tests/` corre **sin red**: SerpAPI se mockea con fixtures reales en `tests/fixtures/` (capturadas en Fase 0). Si necesitás una fixture nueva, capturala con un script en `scripts/validation/` y commitéala.
- `evals/goldens/` contiene briefs de referencia; `evals/assertions.py` valida cada `final.json` contra los tool outputs de su corrida (hard constraints respetadas, precios trazables, sin reviews verbatim, idioma correcto, caps respetados).
- Cualquier cambio en prompts o en la rúbrica ⇒ correr la suite de evals antes de commitear. Los prompts se testean con evals, no a ojo.
- LangSmith siempre activo en dev: si un run se comporta raro, primero mirar el trace.

## Qué NO hacer

- No llamar a SerpAPI desde tests ni desde CI.
- No commitear `.env`, claves, ni `runs/` (outputs de corridas).
- No agregar el subagente `general-purpose` (está deshabilitado a propósito: costos predecibles).
- No subir de modelo (haiku→sonnet) "porque sí": la decisión se toma corriendo la matriz de evals (haiku vs. sonnet sobre los goldens) y se documenta en `config.py`.
- No agregar dependencias pesadas sin justificar (el runtime debe seguir siendo barato de deployar).
- No optimizar prematuramente el frontend: la prioridad es el pipeline y los evals.

## Estado actual

<!-- Mantener actualizado a mano: marcar solo lo implementado y estable -->
- [ ] Capa de datos (`tools/`): cliente SerpAPI + 3 tools + fixtures
- [ ] Contratos (`schemas.py`) + rúbrica del fit_score
- [ ] Agentes (`agents/`): discovery, analyst, orquestador
- [ ] Runner CLI + tracing LangSmith
- [ ] Suite de evals (decisión haiku/sonnet en el analista: pendiente)
- [ ] Chat de brief (`brief/`)
- [ ] Backend (`api/`) + cache de discovery
- [ ] Frontend
