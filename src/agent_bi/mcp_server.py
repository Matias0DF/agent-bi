import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from agent_bi import db, rag

server = MCPServer(
    name="agent-bi",
    instructions=(
        "Herramientas para analizar los datos de ventas de Olist, un e-commerce brasileño (2016-2018). "
        "Antes de escribir SQL, usá search_docs para leer las definiciones del negocio y get_schema para ver las tablas. "
        "Para una respuesta completa y validada en un solo paso, usá ask_agent."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


@server.tool(annotations=READ_ONLY)
def get_schema() -> str:
    """Devuelve las tablas y columnas de la base de datos."""
    return db.get_schema()


@server.tool(annotations=READ_ONLY)
def search_docs(query: str) -> str:
    """Busca en la documentación del negocio: métricas, períodos, regiones y reglas sobre las tablas."""
    return "\n\n".join(f"### {d['title']}\n{d['content']}" for d in rag.search_docs(query))


@server.tool(annotations=READ_ONLY)
def run_sql(query: str) -> str:
    """Ejecuta una consulta SELECT de solo lectura en PostgreSQL (máximo 200 filas)."""
    try:
        columns, rows = db.run_query(query)
    except Exception as e:
        raise ToolError(str(e)) from e
    return json.dumps([dict(zip(columns, r)) for r in rows], default=str, ensure_ascii=False)


@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def ask_agent(question: str) -> str:
    """Responde una pregunta de negocio con el agente completo: busca definiciones, escribe y valida la SQL."""
    from agent_bi.graph import ask

    result = ask(question)
    return json.dumps(
        {
            "answer": result.answer,
            "sql": result.sql,
            "approved": bool(result.reviews) and result.reviews[-1]["approved"],
            "visuals": [
                {"title": v["title"], "rows": [dict(zip(v["columns"], r)) for r in v["rows"][:50]]} for v in result.visuals
            ],
        },
        default=str,
        ensure_ascii=False,
    )


def main():
    """Arranca el servidor MCP por stdio."""
    server.run()


if __name__ == "__main__":
    main()
