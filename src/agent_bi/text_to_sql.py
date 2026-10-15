import os

import anthropic
from pydantic import BaseModel

from agent_bi.db import get_schema

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """Sos un analista de datos experto en PostgreSQL.
Tu trabajo es traducir preguntas de negocio a una única consulta SQL sobre esta base,
que contiene datos de un e-commerce brasileño (Olist) entre 2016 y 2018.

Esquema:
{schema}

Reglas:
- Solo consultas SELECT, nunca modifiques datos.
- Usá únicamente las tablas y columnas del esquema.
- Los montos están en reales brasileños (BRL).
- Si la pregunta es ambigua, elegí la interpretación más razonable y explicala en "explanation".
"""


class SQLAnswer(BaseModel):
    sql: str
    explanation: str


_client = anthropic.Anthropic()


def generate_sql(question: str) -> SQLAnswer:
    """Le pide a Claude la consulta SQL que responde la pregunta."""
    response = _client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT.format(schema=get_schema()),
        messages=[{"role": "user", "content": question}],
        output_format=SQLAnswer,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("El modelo rechazó la pregunta.")
    return response.parsed_output
