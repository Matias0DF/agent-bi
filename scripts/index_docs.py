from pathlib import Path

from pgvector.psycopg import register_vector

from agent_bi.db import connect
from agent_bi.rag import EMBEDDING_DIM, embed

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


def split_sections(markdown: str) -> list[tuple[str, str]]:
    """Parte el markdown en un fragmento por cada título '## '."""
    chunks = []
    for block in markdown.split("\n## ")[1:]:
        title, _, body = block.partition("\n")
        chunks.append((title.strip(), f"{title.strip()}\n{body.strip()}"))
    return chunks


def main():
    chunks = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        for title, content in split_sections(path.read_text(encoding="utf-8")):
            chunks.append((path.name, title, content))

    vectors = embed([content for _, _, content in chunks])

    with connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        conn.execute("DROP TABLE IF EXISTS doc_chunks")
        conn.execute(
            f"""
            CREATE TABLE doc_chunks (
                id SERIAL PRIMARY KEY,
                source TEXT,
                title TEXT,
                content TEXT,
                embedding vector({EMBEDDING_DIM}),
                tsv tsvector GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED
            )
            """
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO doc_chunks (source, title, content, embedding) VALUES (%s, %s, %s, %s)",
                [(s, t, c, v) for (s, t, c), v in zip(chunks, vectors)],
            )

    print(f"{len(chunks)} fragmentos indexados")


if __name__ == "__main__":
    main()
