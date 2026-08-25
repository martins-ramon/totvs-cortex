"""Remove as tabelas legadas do FeedbackAI que o Cortex não utiliza mais.

Uso:  python -m cortex.drop_legacy     (requer DATABASE_URL)

Idempotente. As tabelas do sistema atual (users, people, one_on_ones,
commitments, checkpoints, card_jobs, member_cards) NÃO são tocadas.
"""
import sys

from sqlalchemy import text

from .database import database_url, get_engine

LEGACY_TABLES = [
    "meeting_chunks",
    "meeting_access",
    "meetings",
    "insights",
    "feedbacks",
    "agent_insights",
    "agents",
    "notifications",
]


def run():
    engine = get_engine()
    with engine.connect() as conn:
        for table in LEGACY_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            print(f"  dropped: {table}")
        conn.commit()

        # Extensão pgvector: só servia às tabelas legadas. Best-effort.
        try:
            conn.execute(text("DROP EXTENSION IF EXISTS vector"))
            conn.commit()
            print("  dropped extension: vector")
        except Exception as e:
            conn.rollback()
            print(f"  extensão 'vector' mantida (dependências externas ou sem permissão): {e}")

        remaining = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )).fetchall()]
        print("Tabelas restantes:", ", ".join(remaining))


def main():
    if not database_url():
        print("ERRO: DATABASE_URL não configurada.", file=sys.stderr)
        sys.exit(1)
    print("Removendo tabelas legadas do FeedbackAI...")
    run()
    print("Concluído.")


if __name__ == "__main__":
    main()
