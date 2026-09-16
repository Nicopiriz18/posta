"""Suite de evals: `uv run python -m lodging.evals`.

Modos:
- (default) corre cada golden de `evals/goldens/` end-to-end (REQUIERE red + ANTHROPIC_API_KEY + SERPAPI_KEY;
  cada golden consume ~10-20 búsquedas de SerpAPI) y valida `final.json` con las assertions.
- `--from-runs runs/` re-valida corridas ya guardadas, sin red.
- `--analyst-model anthropic:claude-sonnet-5` corre la matriz haiku vs sonnet (decisión documentada en config).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from lodging import config
from lodging.evals.assertions import check_run
from lodging.schemas import Brief, FinalReport

GOLDENS_DIR = Path(__file__).parent / "goldens"


def _print_result(name: str, res: dict, extra: str = "") -> None:
    status = "OK " if res["ok"] else "FAIL"
    print(f"[{status}] {name} {extra}")
    for check, r in res["checks"].items():
        if not r["ok"]:
            for d in r["details"]:
                print(f"       - {check}: {d}")


def validate_run_dir(d: Path) -> dict | None:
    final = d / "final.json"
    tools = d / "tool_outputs.jsonl"
    if not final.exists() or not tools.exists():
        return None
    report = FinalReport.model_validate_json(final.read_text(encoding="utf-8"))
    outputs = [json.loads(line) for line in tools.read_text(encoding="utf-8").splitlines() if line.strip()]
    return check_run(report, outputs)


async def run_goldens(names: list[str] | None, analyst_model: str | None, timeout: float) -> int:
    from lodging.agents import build_agent
    from lodging.runner import run_brief
    from lodging.runtime import RunContext

    goldens = sorted(GOLDENS_DIR.glob("*.json"))
    if names:
        goldens = [g for g in goldens if g.stem in names]
    if not goldens:
        print("No hay goldens que correr.")
        return 1
    agent = build_agent(analyst_model=analyst_model) if analyst_model else build_agent()
    failures = 0
    rows = []
    for g in goldens:
        brief = Brief.model_validate(json.loads(g.read_text(encoding="utf-8")))
        ctx = RunContext(brief, run_id=f"eval-{g.stem}-{RunContext(brief).run_id}")
        print(
            f"→ {g.stem}: {brief.destination} {brief.check_in}→{brief.check_out} (modelo analista: {analyst_model or config.get_settings().analyst_model})"
        )
        result = await run_brief(brief, ctx=ctx, agent=agent, timeout=timeout)
        res = result.assertions or {"ok": False, "checks": {}}
        s = result.report.stats
        extra = f"— {len(result.report.ranking)} rankeadas, {s.analyzed} analizadas, {s.serpapi_calls} llamadas, {s.duration_seconds}s"
        _print_result(g.stem, res, extra)
        rows.append(
            {
                "golden": g.stem,
                "run_id": ctx.run_id,
                "ok": res["ok"],
                "ranked": len(result.report.ranking),
                "analyzed": s.analyzed,
                "seconds": s.duration_seconds,
                "serpapi_calls": s.serpapi_calls,
                "top_fit": result.report.ranking[0].verdict.fit_score if result.report.ranking else None,
            }
        )
        failures += 0 if res["ok"] else 1
    out = config.get_settings().runs_dir / "evals_summary.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(
                json.dumps(
                    {**r, "analyst_model": analyst_model or config.get_settings().analyst_model},
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"\n{len(rows) - failures}/{len(rows)} goldens OK. Resumen en {out}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Evals de lodging-agent")
    p.add_argument("--from-runs", metavar="DIR", help="Re-validar corridas guardadas (sin red)")
    p.add_argument("--golden", action="append", help="Correr solo este golden (repetible)")
    p.add_argument("--analyst-model", help="Override del modelo del analista (matriz haiku/sonnet)")
    p.add_argument("--timeout", type=float, default=config.RUN_TIMEOUT_SECONDS)
    args = p.parse_args(argv)

    if args.from_runs:
        root = Path(args.from_runs)
        dirs = [d for d in sorted(root.iterdir()) if d.is_dir()] if root.exists() else []
        n = bad = 0
        for d in dirs:
            res = validate_run_dir(d)
            if res is None:
                continue
            n += 1
            bad += 0 if res["ok"] else 1
            _print_result(d.name, res)
        print(f"\n{n - bad}/{n} corridas OK")
        return 1 if bad else 0

    s = config.get_settings()
    missing = [
        k for k, v in (("SERPAPI_KEY", s.serpapi_key), ("ANTHROPIC_API_KEY", s.anthropic_api_key)) if not v
    ]
    if missing:
        print(
            f"Faltan variables en .env: {', '.join(missing)}. Para validar sin red: --from-runs runs/",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(run_goldens(args.golden, args.analyst_model, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
