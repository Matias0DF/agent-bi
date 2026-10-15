import os
import time
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent_bi.graph import ask

WEB_DIR = Path(__file__).parent / "web"
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "20"))

app = FastAPI(title="Agent BI", description="Preguntas de negocio en lenguaje natural sobre datos de ventas.")
_usage: dict[tuple[str, date], int] = defaultdict(int)


class Turn(BaseModel):
    question: str
    answer: str
    sql: str


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    history: list[Turn] = []


def to_json_value(value):
    """Convierte valores de Postgres a tipos que entiende JSON."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def check_access(code: str | None, client_ip: str):
    """Valida el código de acceso y el límite diario por IP."""
    if ACCESS_CODE and code != ACCESS_CODE:
        raise HTTPException(401, "Código de acceso inválido.")
    key = (client_ip, date.today())
    if _usage[key] >= DAILY_LIMIT:
        raise HTTPException(429, f"Llegaste al límite de {DAILY_LIMIT} preguntas por día.")
    _usage[key] += 1


@app.get("/")
def index():
    """Sirve la interfaz web."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    """Indica si el servicio está vivo."""
    return {"status": "ok", "access_code_required": bool(ACCESS_CODE)}


@app.post("/api/ask")
def ask_endpoint(body: AskRequest, request: Request, x_access_code: str | None = Header(default=None)):
    """Responde una pregunta de negocio con el agente."""
    check_access(x_access_code, request.client.host if request.client else "unknown")
    started = time.perf_counter()
    try:
        result = ask(body.question, [t.model_dump() for t in body.history])
    except Exception as e:
        raise HTTPException(500, f"El agente no pudo responder: {e}")

    return {
        "answer": result.answer,
        "sql": result.sql,
        "chart": result.chart,
        "columns": result.columns,
        "rows": [[to_json_value(v) for v in row] for row in result.rows],
        "steps": result.steps,
        "reviews": result.reviews,
        "approved": bool(result.reviews) and result.reviews[-1]["approved"],
        "seconds": round(time.perf_counter() - started, 1),
    }
