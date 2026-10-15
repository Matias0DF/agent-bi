import json
import os
from dataclasses import dataclass, field

import anthropic

from agent_bi.db import get_schema, run_query
from agent_bi.rag import search_docs

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MAX_TURNS = 10
ROWS_FOR_MODEL = 50

SYSTEM_PROMPT = """Sos un analista de datos que responde preguntas de negocio sobre un e-commerce brasileño (Olist, 2016-2018) usando una base PostgreSQL.

Esquema de la base:
{schema}

Cómo trabajar:
1. Antes de escribir SQL, usá search_docs para buscar en la documentación del negocio las definiciones que necesites (métricas, períodos, regiones, reglas). Seguí esas definiciones al pie de la letra.
2. Usá run_sql para ejecutar consultas. Si da error o un resultado sospechoso (vacío, negativo, absurdo), revisá la consulta y probá de nuevo.
3. Cuando tengas el resultado, llamá a submit_answer. Es la única forma de terminar.

La respuesta debe:
- Contestar la pregunta en 1 a 3 oraciones, en español, con los números clave.
- Montos en reales (R$), con separador de miles.
- Si interpretaste algo ambiguo, aclararlo brevemente.

Para el gráfico, diseñá la consulta final pensando en cómo se va a ver:
- Si comparás períodos o grupos (por ejemplo 2017 vs 2018, Q1 vs Q2), poné cada uno en su propia columna y pasalas todas en "y".
- Porcentajes en escala 0-100, redondeados a 2 decimales.
"""

TOOLS = [
    {
        "name": "search_docs",
        "description": "Busca en la documentación del negocio: definiciones de métricas, períodos, regiones, reglas y advertencias sobre las tablas. Usala antes de escribir SQL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Qué buscar, en lenguaje natural."}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "run_sql",
        "description": "Ejecuta una consulta SELECT de solo lectura en PostgreSQL y devuelve columnas y filas (máximo 200).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Consulta SELECT a ejecutar."}
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "submit_answer",
        "description": "Entrega la respuesta final al usuario. Llamala una sola vez, al terminar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "Respuesta en lenguaje natural."},
                "sql": {"type": "string", "description": "La consulta SQL final que respalda la respuesta."},
                "chart": {
                    "type": "object",
                    "description": "Visualización del resultado de la consulta final.",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["bar", "line", "pie", "kpi", "none"],
                            "description": (
                                "bar: comparar categorías (una o varias series). "
                                "line: evolución en el tiempo (una o varias series). "
                                "pie: proporciones de un total, una sola serie y pocas categorías. "
                                "kpi: el resultado es uno o pocos números destacados en una sola fila. "
                                "none: una tabla es más clara que un gráfico."
                            ),
                        },
                        "x": {"type": "string", "description": "Columna de categorías o fechas. Vacío para kpi."},
                        "y": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Columnas numéricas a mostrar. Varias columnas = varias series (por ejemplo 2017 y 2018).",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["number", "currency", "percent"],
                            "description": "currency para montos en R$, percent si los valores ya están en escala 0-100.",
                        },
                        "title": {"type": "string"},
                    },
                    "required": ["type", "x", "y", "format", "title"],
                    "additionalProperties": False,
                },
            },
            "required": ["answer", "sql", "chart"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class AgentResult:
    answer: str = ""
    sql: str = ""
    chart: dict = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)


_client = anthropic.Anthropic()


def _search_docs(query: str, steps: list) -> str:
    """Ejecuta la herramienta search_docs."""
    docs = search_docs(query)
    steps.append({"tool": "search_docs", "input": query, "output": [d["title"] for d in docs]})
    return "\n\n".join(f"### {d['title']}\n{d['content']}" for d in docs)


def _run_sql(sql: str, steps: list) -> str:
    """Ejecuta la herramienta run_sql."""
    try:
        columns, rows = run_query(sql)
    except Exception as e:
        steps.append({"tool": "run_sql", "input": sql, "error": str(e)})
        raise
    steps.append({"tool": "run_sql", "input": sql, "output": f"{len(rows)} filas"})
    preview = [dict(zip(columns, r)) for r in rows[:ROWS_FOR_MODEL]]
    return json.dumps({"rows_total": len(rows), "rows": preview}, default=str, ensure_ascii=False)


def execute_tool(block, steps: list) -> dict:
    """Ejecuta un pedido de herramienta y arma el tool_result."""
    try:
        if block.name == "search_docs":
            content = _search_docs(block.input["query"], steps)
        elif block.name == "run_sql":
            content = _run_sql(block.input["sql"], steps)
        else:
            raise ValueError(f"Herramienta desconocida: {block.name}")
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}
    except Exception as e:
        return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {e}", "is_error": True}


def call_model(system: str, messages: list):
    """Le manda la conversación a Claude con las herramientas disponibles."""
    response = _client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        cache_control={"type": "ephemeral"},
        system=system,
        tools=TOOLS,
        messages=messages,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("El modelo rechazó la pregunta.")
    return response


def ask(question: str) -> AgentResult:
    """Corre el bucle del agente hasta que entregue una respuesta."""
    result = AgentResult()
    messages = [{"role": "user", "content": question}]
    system = SYSTEM_PROMPT.format(schema=get_schema())

    for _ in range(MAX_TURNS):
        response = call_model(system, messages)
        messages.append({"role": "assistant", "content": response.content})
        tool_calls = [b for b in response.content if b.type == "tool_use"]

        for block in tool_calls:
            if block.name == "submit_answer":
                result.answer = block.input["answer"]
                result.sql = block.input["sql"]
                result.chart = block.input["chart"]
                result.columns, result.rows = run_query(result.sql)
                return result

        if not tool_calls:
            messages.append({"role": "user", "content": "Para terminar, llamá a submit_answer."})
            continue

        messages.append({"role": "user", "content": [execute_tool(b, result.steps) for b in tool_calls]})

    raise RuntimeError(f"El agente no terminó en {MAX_TURNS} turnos.")
