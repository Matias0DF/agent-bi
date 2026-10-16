import json
import operator
import os
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent_bi.agent import (
    MAX_TURNS,
    MODEL,
    SYSTEM_PROMPT,
    AgentResult,
    _client,
    call_model,
    execute_tool,
    fallback_args,
    run_visuals,
)
from agent_bi import usage
from agent_bi.db import get_schema
from agent_bi.rag import get_docs, search_docs

MAX_REVISIONS = 2
VALIDATOR_MODEL = os.getenv("VALIDATOR_MODEL", MODEL)

VALIDATOR_PROMPT = """Sos un revisor de calidad de un equipo de datos. Un analista respondió una pregunta de negocio con uno o más visuales, cada uno con su consulta SQL.
Tu trabajo es detectar errores antes de que la respuesta llegue al usuario.

Revisá:
- Que el primer visual responda exactamente lo que se preguntó, y que los demás sean coherentes con él.
- Que respete las definiciones de la documentación (métricas, períodos, estados de orden, regiones).
- Que no haya errores típicos: JOINs que dupliquen montos, contar customer_id en vez de customer_unique_id, fechas mal filtradas.
- Que los números del resultado sean razonables y coincidan con lo que dice la respuesta.

Aprobá si la respuesta es correcta aunque se pueda mejorar en detalles menores. Rechazá solo si hay un error que cambia el resultado o la conclusión."""

REVIEW_TEMPLATE = """Documentación del negocio:
{docs}

Pregunta del usuario: {question}

Visuales del analista:
{visuals}

Respuesta del analista: {answer}"""


class Review(BaseModel):
    approved: bool
    issues: list[str]


class State(TypedDict):
    question: str
    system: str
    messages: Annotated[list, operator.add]
    steps: Annotated[list, operator.add]
    reviews: Annotated[list, operator.add]
    turns: int
    answer: str
    visuals: list
    finalize_error: str


def _pending_tool_calls(state: State) -> list:
    """Devuelve los pedidos de herramienta del último mensaje del analista."""
    return [b for b in state["messages"][-1]["content"] if getattr(b, "type", None) == "tool_use"]


def analyst(state: State) -> dict:
    """Nodo analista: Claude decide qué herramienta usar o entrega la respuesta."""
    if state["turns"] >= MAX_TURNS:
        raise RuntimeError(f"El agente no terminó en {MAX_TURNS} turnos.")
    response = call_model(state["system"], state["messages"])
    return {"messages": [{"role": "assistant", "content": response.content}], "turns": state["turns"] + 1}


def tools(state: State) -> dict:
    """Nodo herramientas: ejecuta search_docs y run_sql."""
    steps = []
    results = [execute_tool(b, steps) for b in _pending_tool_calls(state)]
    return {"messages": [{"role": "user", "content": results}], "steps": steps}


def nudge(state: State) -> dict:
    """Nodo recordatorio: el analista respondió sin usar submit_answer."""
    return {"messages": [{"role": "user", "content": "Para terminar, llamá a submit_answer."}]}


def finalize(state: State) -> dict:
    """Nodo final: ejecuta la SQL de cada visual; si alguna falla, se la devuelve al analista."""
    calls = _pending_tool_calls(state)
    submit = next(b for b in calls if b.name == "submit_answer")
    try:
        visuals = run_visuals(submit.input["visuals"])
    except ValueError as e:
        results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": str(e), "is_error": True}
            if b.name == "submit_answer"
            else execute_tool(b, [])
            for b in calls
        ]
        return {"finalize_error": str(e), "messages": [{"role": "user", "content": results}]}
    return {"answer": submit.input["answer"], "visuals": visuals, "finalize_error": ""}


def describe_visuals(visuals: list[dict]) -> str:
    """Resume cada visual con su SQL y las primeras filas para el validador."""
    parts = []
    for i, v in enumerate(visuals, 1):
        preview = [dict(zip(v["columns"], r)) for r in v["rows"][:40]]
        parts.append(
            f"### Visual {i}: {v['title']} ({v['type']})\nSQL:\n{v['sql']}\n"
            f"Resultado ({len(v['rows'])} filas, hasta 40): {json.dumps(preview, default=str, ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


def validator(state: State) -> dict:
    """Nodo validador: un segundo modelo revisa la SQL y el resultado."""
    titles = {t for s in state["steps"] if s["tool"] == "search_docs" for t in s["output"]}
    titles |= {d["title"] for d in search_docs(state["question"])}
    docs = get_docs(sorted(titles))
    response = _client.beta.messages.parse(
        model=VALIDATOR_MODEL,
        max_tokens=4000,
        **fallback_args(VALIDATOR_MODEL),
        output_config={"effort": "medium"},
        system=VALIDATOR_PROMPT,
        messages=[{
            "role": "user",
            "content": REVIEW_TEMPLATE.format(
                docs="\n\n".join(f"### {d['title']}\n{d['content']}" for d in docs),
                question=state["question"],
                visuals=describe_visuals(state["visuals"]),
                answer=state["answer"],
            ),
        }],
        output_format=Review,
    )
    usage.record(response.model, response.usage)
    review = response.parsed_output
    update = {"reviews": [review.model_dump()]}

    if not review.approved and len(state["reviews"]) < MAX_REVISIONS:
        feedback = "El revisor rechazó la respuesta:\n- " + "\n- ".join(review.issues)
        results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": feedback, "is_error": True}
            if b.name == "submit_answer"
            else execute_tool(b, [])
            for b in _pending_tool_calls(state)
        ]
        update["messages"] = [{"role": "user", "content": results}]
    return update


def route_after_analyst(state: State) -> str:
    """Decide a dónde va el flujo después del analista."""
    calls = _pending_tool_calls(state)
    if any(b.name == "submit_answer" for b in calls):
        return "finalize"
    return "tools" if calls else "nudge"


def route_after_finalize(state: State) -> str:
    """Si alguna consulta falló, vuelve al analista; si no, pasa al validador."""
    return "analyst" if state["finalize_error"] else "validator"


def route_after_validator(state: State) -> str:
    """Si el revisor rechazó y quedan revisiones, vuelve al analista."""
    last = state["reviews"][-1]
    if last["approved"] or len(state["reviews"]) > MAX_REVISIONS:
        return END
    return "analyst"


def build_graph():
    """Arma el grafo del agente."""
    graph = StateGraph(State)
    graph.add_node("analyst", analyst)
    graph.add_node("tools", tools)
    graph.add_node("nudge", nudge)
    graph.add_node("finalize", finalize)
    graph.add_node("validator", validator)

    graph.add_edge(START, "analyst")
    graph.add_conditional_edges("analyst", route_after_analyst, ["tools", "nudge", "finalize"])
    graph.add_edge("tools", "analyst")
    graph.add_edge("nudge", "analyst")
    graph.add_conditional_edges("finalize", route_after_finalize, ["analyst", "validator"])
    graph.add_conditional_edges("validator", route_after_validator, ["analyst", END])
    return graph.compile()


app = build_graph()


def with_history(question: str, history: list[dict]) -> str:
    """Agrega las preguntas anteriores de la conversación como contexto."""
    if not history:
        return question
    previous = "\n\n".join(
        f"Pregunta: {h['question']}\nRespuesta: {h['answer']}\nSQL: {h['sql']}" for h in history[-3:]
    )
    return f"Conversación previa (usala como contexto):\n{previous}\n\nPregunta actual: {question}"


def ask(question: str, history: list[dict] | None = None) -> AgentResult:
    """Corre el grafo completo y devuelve el resultado."""
    question = with_history(question, history or [])
    state = app.invoke(
        {
            "question": question,
            "system": SYSTEM_PROMPT.format(schema=get_schema()),
            "messages": [{"role": "user", "content": question}],
            "steps": [],
            "reviews": [],
            "turns": 0,
            "finalize_error": "",
        },
        {"recursion_limit": 50},
    )
    return AgentResult(answer=state["answer"], visuals=state["visuals"], steps=state["steps"], reviews=state["reviews"])
