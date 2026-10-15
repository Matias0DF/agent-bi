from contextvars import ContextVar

PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}

_calls: ContextVar[list | None] = ContextVar("usage_calls", default=None)


def start():
    """Empieza a registrar el consumo de la pregunta actual."""
    _calls.set([])


def record(model: str, usage):
    """Guarda los tokens de una llamada a la API."""
    calls = _calls.get()
    if calls is None:
        return
    calls.append({
        "model": model,
        "input": usage.input_tokens or 0,
        "output": usage.output_tokens or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    })


def summary() -> dict:
    """Devuelve tokens totales y costo estimado en dólares."""
    calls = _calls.get() or []
    totals = {k: sum(c[k] for c in calls) for k in ("input", "output", "cache_read", "cache_write")}
    cost = sum(
        c[k] * PRICES.get(c["model"], PRICES["claude-opus-5-5"])[k] / 1_000_000
        for c in calls
        for k in ("input", "output", "cache_read", "cache_write")
    )
    return {"calls": len(calls), **totals, "cost_usd": round(cost, 4)}
