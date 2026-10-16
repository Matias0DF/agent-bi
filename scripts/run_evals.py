import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

import yaml

from agent_bi import usage
from agent_bi.agent import MODEL
from agent_bi.db import run_query

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


def run_single(question: str):
    """Variante 1: una sola llamada que genera la SQL."""
    from agent_bi.text_to_sql import generate_sql

    answer, _ = generate_sql(question)
    columns, rows = run_query(answer.sql)
    return answer.explanation, answer.sql, columns, rows


def run_agent(question: str):
    """Variante 2: agente con herramientas, sin validador."""
    from agent_bi.agent import ask

    r = ask(question)
    return r.answer, r.sql, [], r.rows


def run_graph(question: str):
    """Variante 3: agente con validador (LangGraph)."""
    from agent_bi.graph import ask

    r = ask(question)
    return r.answer, r.sql, [], r.rows


VARIANTS = {"single": run_single, "agent": run_agent, "graph": run_graph}


def number_matches(expected: float, actual: float) -> bool:
    """Compara números con tolerancia, aceptando escala 0-1 vs 0-100."""
    for candidate in (actual, actual * 100, actual / 100):
        if abs(candidate - expected) <= max(abs(expected) * 0.01, 0.05):
            return True
    return False


def is_correct(gold_rows: list, rows: list, answer: str) -> bool:
    """Verifica que cada valor de la respuesta correcta aparezca en el resultado del agente."""
    cells = [v for row in rows for v in row]
    numbers = [float(v) for v in cells if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)]
    texts = [str(v).lower() for v in cells if isinstance(v, str)] + [answer.lower()]

    for value in (v for row in gold_rows for v in row):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float, Decimal)):
            if not any(number_matches(float(value), n) for n in numbers):
                return False
        elif not any(str(value).lower() in t for t in texts):
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=VARIANTS, default="graph")
    parser.add_argument("--ids", help="Lista de ids separados por coma, por ejemplo 1,11,23")
    args = parser.parse_args()

    questions = yaml.safe_load((EVALS_DIR / "questions.yaml").read_text(encoding="utf-8"))
    if args.ids:
        wanted = {int(i) for i in args.ids.split(",")}
        questions = [q for q in questions if q["id"] in wanted]

    results = []
    for q in questions:
        _, gold_rows = run_query(q["sql"])
        usage.start()
        started = time.perf_counter()
        try:
            answer, sql, _, rows = VARIANTS[args.variant](q["question"])
            correct, error = is_correct(gold_rows, rows, answer), None
        except Exception as e:
            answer, sql, rows, correct, error = "", "", [], False, str(e)
        seconds = round(time.perf_counter() - started, 1)
        cost = usage.summary()

        results.append({**q, "correct": correct, "error": error, "answer": answer, "predicted_sql": sql,
                        "seconds": seconds, "usage": cost})
        mark = "✓" if correct else "✗"
        print(f"{mark} {q['id']:>2} [{q['difficulty']}] {seconds:>5}s  ${cost['cost_usd']:.3f}  {q['question']}")

    total = len(results)
    hits = sum(r["correct"] for r in results)
    summary = {
        "variant": args.variant,
        "model": MODEL,
        "questions": total,
        "correct": hits,
        "accuracy": round(100 * hits / total, 1) if total else 0,
        "cost_usd": round(sum(r["usage"]["cost_usd"] for r in results), 3),
        "avg_seconds": round(sum(r["seconds"] for r in results) / total, 1) if total else 0,
        "by_difficulty": {
            d: f"{sum(r['correct'] for r in results if r['difficulty'] == d)}/{sum(r['difficulty'] == d for r in results)}"
            for d in dict.fromkeys(r["difficulty"] for r in results)
        },
    }
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))

    suffix = f"_ids-{args.ids.replace(',', '-')}" if args.ids else ""
    out = EVALS_DIR / "results" / f"{args.variant}__{MODEL}{suffix}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"Resultados guardados en {out.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    main()
