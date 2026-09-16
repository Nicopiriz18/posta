# lodging-agent

Backend, agente y frontend de **posta.**, un agente de deep research de alojamientos.

La documentación completa (arquitectura, decisiones de diseño, API, evals) está en el [README principal](../README.md).
El contrato backend ⇄ frontend está en [`docs/api.md`](docs/api.md).

## Quickstart

```bash
cp .env.example .env                                  # ANTHROPIC_API_KEY y SERPAPI_KEY
uv sync --group dev
uv run pytest                                         # tests sin red
uv run uvicorn lodging.api.main:app --reload          # backend en http://localhost:8000
cd frontend && npm install && npm run dev             # frontend en http://localhost:5173
```
