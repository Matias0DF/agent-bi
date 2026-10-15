from functools import lru_cache

from fastembed import TextEmbedding
from pgvector.psycopg import register_vector

from agent_bi.db import connect

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


@lru_cache
def _model() -> TextEmbedding:
    """Carga el modelo de embeddings una sola vez."""
    return TextEmbedding(EMBEDDING_MODEL)


def embed(texts: list[str]) -> list:
    """Convierte textos en vectores."""
    return list(_model().embed(texts))


HYBRID_QUERY = """
WITH semantic AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s) AS rank
    FROM doc_chunks
    ORDER BY embedding <=> %(vec)s
    LIMIT 20
),
keyword AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rank
    FROM doc_chunks,
         (SELECT replace(plainto_tsquery('spanish', %(text)s)::text, '&', '|')::tsquery AS q) AS query
    WHERE tsv @@ q
    LIMIT 20
)
SELECT d.title, d.content,
       COALESCE(1.0 / (60 + s.rank), 0) + COALESCE(1.0 / (60 + k.rank), 0) AS score
FROM doc_chunks d
LEFT JOIN semantic s ON s.id = d.id
LEFT JOIN keyword k ON k.id = d.id
WHERE s.id IS NOT NULL OR k.id IS NOT NULL
ORDER BY score DESC
LIMIT %(k)s
"""


def get_docs(titles: list[str]) -> list[dict]:
    """Devuelve fragmentos de documentación por título."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT title, content FROM doc_chunks WHERE title = ANY(%s)", (titles,)
        ).fetchall()
    return [{"title": t, "content": c} for t, c in rows]


def search_docs(question: str, k: int = 5) -> list[dict]:
    """Búsqueda híbrida: combina similitud semántica y palabras clave."""
    query_vector = embed([question])[0]
    with connect() as conn:
        register_vector(conn)
        rows = conn.execute(
            HYBRID_QUERY, {"vec": query_vector, "text": question, "k": k}
        ).fetchall()
    return [{"title": t, "content": c, "score": float(s)} for t, c, s in rows]
