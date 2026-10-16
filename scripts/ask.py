import sys

from agent_bi.graph import ask


def print_table(columns: list[str], rows: list[tuple]):
    """Imprime el resultado como una tabla simple."""
    widths = [max(len(str(v)) for v in [c, *(r[i] for r in rows)]) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))


def print_steps(steps: list[dict]):
    """Muestra cada herramienta que usó el agente."""
    for i, step in enumerate(steps, 1):
        detail = step.get("error") and f"ERROR: {step['error']}" or step["output"]
        text = " ".join(step["input"].split())
        print(f"  {i}. {step['tool']}: {text[:90]}")
        print(f"     → {detail}")


def main():
    question = " ".join(sys.argv[1:]) or input("Pregunta: ")
    result = ask(question)

    print("\nPasos del agente:")
    print_steps(result.steps)
    for i, review in enumerate(result.reviews, 1):
        status = "aprobada" if review["approved"] else "rechazada"
        print(f"\nRevisión {i}: {status}")
        for issue in review["issues"]:
            print(f"  - {issue}")
    print(f"\nRespuesta:\n{result.answer}\n")
    for visual in result.visuals:
        print(f"[{visual['type']}] {visual['title']}  x={visual['x']} y={visual['y']} series={visual['series']}")
        print_table(visual["columns"], visual["rows"][:15])
        print()


if __name__ == "__main__":
    main()
