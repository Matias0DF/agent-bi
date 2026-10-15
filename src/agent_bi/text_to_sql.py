import os

import anthropic
from pydantic import BaseModel

from agent_bi import usage
from agent_bi.agent import fallback_args
from agent_bi.db import get_schema
from agent_bi.rag import search_docs

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """Sos un analista de datos experto en PostgreSQL.
Tu trabajo es traducir preguntas de negocio a una única consulta SQL sobre esta base,
que contiene datos de un e-commerce brasileño (Olist) entre 2016 y 2018.

Esquema:
{schema}

Reglas:
- Solo consultas SELECT, nunca modifiques datos.
- Usá únicamente las tablas y columnas del esquema.
- Junto con cada pregunta vas a recibir fragmentos de la documentación del negocio.
  Cuando definan una métrica, un período o una regla, seguilos al pie de la letra.
- Si la pregunta es ambigua, elegí la interpretación más razonable y explicala en "explanation".
"""

USER_TEMPLATE = """Documentación relevante:
{context}

Pregunta: {question}"""


class SQLAnswer(BaseModel):
    sql: str
    explanation: str


_client = anthropic.Anthropic()


def format_context(docs: list[dict]) -> str:
    """Une los fragmentos recuperados en un solo bloque de texto."""
    return "\n\n".join(f"### {d['title']}\n{d['content']}" for d in docs)


def generate_sql(question: str) -> tuple[SQLAnswer, list[dict]]:
    """Busca documentación relevante y le pide a Claude la consulta SQL."""
    docs = search_docs(question)
    response = _client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        **fallback_args(MODEL),
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT.format(schema=get_schema()),
        messages=[{
            "role": "user",
            "content": USER_TEMPLATE.format(context=format_context(docs), question=question),
        }],
        output_format=SQLAnswer,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("El modelo rechazó la pregunta.")
    usage.record(response.model, response.usage)
    return response.parsed_output, docs
