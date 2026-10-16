import json
import os
from dataclasses import dataclass, field

import anthropic

from agent_bi import usage
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

Visuales (en submit_answer):
- Cada visual tiene su propia consulta SQL. Ejecutá cada una con run_sql antes de entregarla.
- Pregunta simple: 1 visual. Análisis más amplio: 2 o 3 visuales que se complementen, por ejemplo KPIs + evolución + tabla de detalle. Nunca más de 4.
- El primer visual siempre responde directamente la pregunta.
- Si hay 2 o más visuales, el último es una tabla de detalle ("table") con las dimensiones y medidas usadas en la respuesta. Tiene que sumar información (todas las medidas citadas en la respuesta, o un desglose más fino), no repetir la consulta de otro visual.
- Comparación de períodos o grupos fijos (2017 vs 2018): cada uno en su propia columna, pasalas todas en "y".
- Una dimensión que se reparte en series (por categoría, por región): devolvé formato largo (x, dimensión, valor) y poné la dimensión en "series". Limitá las series a 6 o menos agrupando el resto como 'Otros'.
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
                "visuals": {
                    "type": "array",
                    "description": "De 1 a 4 visuales. El primero responde la pregunta; si hay varios, el último es una tabla de detalle.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["kpi", "bar", "stacked_bar", "line", "combo", "scatter", "waterfall", "heatmap", "pie", "table"],
                                "description": (
                                    "kpi: 1 a 4 números destacados en una sola fila. "
                                    "bar: comparar categorías (una o varias medidas en y, o una dimensión en series). "
                                    "stacked_bar: composición apilada; x = categoría o período, series = dimensión que se apila. "
                                    "line: evolución en el tiempo; varias medidas en y o una dimensión en series. "
                                    "combo: barras para y[0] y línea para y[1] con eje propio (por ejemplo ventas y cantidad de órdenes). "
                                    "scatter: relación entre dos medidas; x e y[0] numéricas, series = etiqueta de cada punto. "
                                    "waterfall: cómo distintas partes suman a un total o explican una variación; x = partes, y[0] = aporte (positivo o negativo). "
                                    "heatmap: medida cruzada por dos dimensiones; x = columnas, series = filas, y[0] = valor. "
                                    "pie: proporciones de un total, una medida y hasta 6 categorías. "
                                    "table: tabla de detalle con todas las columnas de la consulta."
                                ),
                            },
                            "title": {"type": "string"},
                            "sql": {"type": "string", "description": "Consulta SELECT que alimenta este visual."},
                            "x": {"type": "string", "description": "Columna del eje X o de categorías. Vacío para kpi y table."},
                            "y": {"type": "array", "items": {"type": "string"}, "description": "Columnas numéricas."},
                            "series": {"type": "string", "description": "Columna que separa series, filas del heatmap o etiquetas del scatter. Vacío si no aplica."},
                            "delta": {"type": "string", "description": "Solo kpi: columna con la variación % de la primera medida contra el período anterior. Vacío si no aplica."},
                            "format": {
                                "type": "string",
                                "enum": ["number", "currency", "percent"],
                                "description": "currency para montos en R$, percent si los valores ya están en escala 0-100.",
                            },
                        },
                        "required": ["type", "title", "sql", "x", "y", "series", "delta", "format"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["answer", "visuals"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class AgentResult:
    answer: str = ""
    visuals: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)

    @property
    def sql(self) -> str:
        """Todas las consultas de la respuesta, separadas."""
        return "\n\n".join(v["sql"] for v in self.visuals)

    @property
    def rows(self) -> list[tuple]:
        """Las filas de todos los visuales juntas."""
        return [row for v in self.visuals for row in v["rows"]]


def run_visuals(visuals: list[dict]) -> list[dict]:
    """Ejecuta la SQL de cada visual y le agrega columnas y filas."""
    results = []
    for i, visual in enumerate(visuals[:4], 1):
        try:
            columns, rows = run_query(visual["sql"])
        except Exception as e:
            raise ValueError(f"La consulta del visual {i} ('{visual['title']}') falló: {e}") from e
        results.append({**visual, "columns": columns, "rows": rows})
    return results


_client = anthropic.Anthropic()


def fallback_args(model: str) -> dict:
    """Activa el fallback automático en los modelos que lo soportan."""
    if "haiku" in model:
        return {}
    return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


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
        **fallback_args(MODEL),
        output_config={"effort": "medium"},
        cache_control={"type": "ephemeral"},
        system=system,
        tools=TOOLS,
        messages=messages,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("El modelo rechazó la pregunta.")
    usage.record(response.model, response.usage)
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

        submit = next((b for b in tool_calls if b.name == "submit_answer"), None)
        if submit:
            try:
                result.visuals = run_visuals(submit.input["visuals"])
                result.answer = submit.input["answer"]
                return result
            except ValueError as e:
                results = [
                    {"type": "tool_result", "tool_use_id": b.id, "content": str(e), "is_error": True}
                    if b.name == "submit_answer"
                    else execute_tool(b, result.steps)
                    for b in tool_calls
                ]
                messages.append({"role": "user", "content": results})
                continue

        if not tool_calls:
            messages.append({"role": "user", "content": "Para terminar, llamá a submit_answer."})
            continue

        messages.append({"role": "user", "content": [execute_tool(b, result.steps) for b in tool_calls]})

    raise RuntimeError(f"El agente no terminó en {MAX_TURNS} turnos.")
