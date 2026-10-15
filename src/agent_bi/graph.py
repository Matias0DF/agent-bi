import json
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent_bi.agent import MAX_TURNS, MODEL, SYSTEM_PROMPT, AgentResult, _client, call_model, execute_tool
from agent_bi.db import get_schema, run_query
from agent_bi.rag import get_docs, search_docs

MAX_REVISIONS = 2

VALIDATOR_PROMPT = """Sos un revisor de calidad de un equipo de datos. Un analista respondió una pregunta de negocio con una consulta SQL.
Tu trabajo es detectar errores antes de que la respuesta llegue al usuario.

Revisá:
- Que la SQL responda exactamente lo que se preguntó.
- Que respete las definiciones de la documentación (métricas, períodos, estados de orden, regiones).
- Que no haya errores típicos: JOINs que dupliquen montos, contar customer_id en vez de customer_unique_id, fechas mal filtradas.
- Que los números del resultado sean razonables y coincidan con lo que dice la respuesta.

Aprobá si la respuesta es correcta aunque se pueda mejorar en detalles menores. Rechazá solo si hay un error que cambia el resultado o la conclusión."""

REVIEW_TEMPLATE = """Documentación del negocio:
{docs}

Pregunta del usuario: {question}

SQL del analista:
{sql}

Resultado ({total} filas, se muestran hasta 20):
{rows}

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
    sql: str
    chart: dict
    columns: list
    rows: list


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
    """Nodo final: guarda la respuesta y ejecuta la SQL entregada."""
    submit = next(b for b in _pending_tool_calls(state) if b.name == "submit_answer")
    columns, rows = run_query(submit.input["sql"])
    return {
        "answer": submit.input["answer"],
        "sql": submit.input["sql"],
        "chart": submit.input["chart"],
        "columns": columns,
        "rows": rows,
    }


def validator(state: State) -> dict:
    """Nodo validador: un segundo modelo revisa la SQL y el resultado."""
    titles = {t for s in state["steps"] if s["tool"] == "search_docs" for t in s["output"]}
    titles |= {d["title"] for d in search_docs(state["question"])}
    docs = get_docs(sorted(titles))
    preview = [dict(zip(state["columns"], r)) for r in state["rows"][:20]]
    response = _client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=VALIDATOR_PROMPT,
        messages=[{
            "role": "user",
            "content": REVIEW_TEMPLATE.format(
                docs="\n\n".join(f"### {d['title']}\n{d['content']}" for d in docs),
                question=state["question"],
                sql=state["sql"],
                total=len(state["rows"]),
                rows=json.dumps(preview, default=str, ensure_ascii=False),
                answer=state["answer"],
            ),
        }],
        output_format=Review,
    )
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
    graph.add_edge("finalize", "validator")
    graph.add_conditional_edges("validator", route_after_validator, ["analyst", END])
    return graph.compile()


app = build_graph()


def ask(question: str) -> AgentResult:
    """Corre el grafo completo y devuelve el resultado."""
    state = app.invoke(
        {
            "question": question,
            "system": SYSTEM_PROMPT.format(schema=get_schema()),
            "messages": [{"role": "user", "content": question}],
            "steps": [],
            "reviews": [],
            "turns": 0,
        },
        {"recursion_limit": 50},
    )
    return AgentResult(
        answer=state["answer"],
        sql=state["sql"],
        chart=state["chart"],
        columns=state["columns"],
        rows=state["rows"],
        steps=state["steps"],
        reviews=state["reviews"],
    )
