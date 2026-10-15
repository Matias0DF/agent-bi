import sys

from agent_bi.db import run_query
from agent_bi.text_to_sql import generate_sql


def print_table(columns: list[str], rows: list[tuple]):
    """Imprime el resultado como una tabla simple."""
    widths = [max(len(str(v)) for v in [c, *(r[i] for r in rows)]) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))


def main():
    question = " ".join(sys.argv[1:]) or input("Pregunta: ")
    answer, docs = generate_sql(question)

    print("\nDocumentación usada:")
    for d in docs:
        print(f"  - {d['title']} ({d['score']:.3f})")

    print(f"\nSQL:\n{answer.sql}\n")
    print(f"Interpretación: {answer.explanation}\n")

    columns, rows = run_query(answer.sql)
    print_table(columns, rows)


if __name__ == "__main__":
    main()
