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
                    "description": "Gráfico sugerido para el resultado de la consulta final.",
                    "properties": {
                        "type": {"type": "string", "enum": ["bar", "line", "pie", "none"]},
                        "x": {"type": "string", "description": "Columna para el eje X o las categorías."},
                        "y": {"type": "string", "description": "Columna numérica para el eje Y o los valores."},
                        "title": {"type": "string"},
                    },
                    "required": ["type", "x", "y", "title"],
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


_client = anthropic.Anthropic()


def _search_docs(query: str, result: AgentResult) -> str:
    """Ejecuta la herramienta search_docs."""
    docs = search_docs(query)
    result.steps.append({"tool": "search_docs", "input": query, "output": [d["title"] for d in docs]})
    return "\n\n".join(f"### {d['title']}\n{d['content']}" for d in docs)


def _run_sql(sql: str, result: AgentResult) -> str:
    """Ejecuta la herramienta run_sql."""
    try:
        columns, rows = run_query(sql)
    except Exception as e:
        result.steps.append({"tool": "run_sql", "input": sql, "error": str(e)})
        raise
    result.steps.append({"tool": "run_sql", "input": sql, "output": f"{len(rows)} filas"})
    preview = [dict(zip(columns, r)) for r in rows[:ROWS_FOR_MODEL]]
    return json.dumps({"rows_total": len(rows), "rows": preview}, default=str, ensure_ascii=False)


def _execute(block, result: AgentResult) -> dict:
    """Ejecuta un pedido de herramienta y arma el tool_result."""
    try:
        if block.name == "search_docs":
            content = _search_docs(block.input["query"], result)
        elif block.name == "run_sql":
            content = _run_sql(block.input["sql"], result)
        else:
            raise ValueError(f"Herramienta desconocida: {block.name}")
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}
    except Exception as e:
        return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {e}", "is_error": True}


def ask(question: str) -> AgentResult:
    """Corre el bucle del agente hasta que entregue una respuesta."""
    result = AgentResult()
    messages = [{"role": "user", "content": question}]
    system = SYSTEM_PROMPT.format(schema=get_schema())

    for _ in range(MAX_TURNS):
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

        messages.append({"role": "user", "content": [_execute(b, result) for b in tool_calls]})

    raise RuntimeError(f"El agente no terminó en {MAX_TURNS} turnos.")
