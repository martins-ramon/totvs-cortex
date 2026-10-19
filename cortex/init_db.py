"""Inicialização explícita do schema: python -m cortex.init_db.

Requer DATABASE_URL no ambiente. O comando é idempotente e não apaga dados.
Não é executado durante o build da Vercel.
"""
import sys

from . import database


def main():
    if database.init_db():
        print("Schema Cortex verificado/atualizado com sucesso.")
        return 0
    if database.INIT_DB_STATUS == "unconfigured":
        print("ERRO: configure DATABASE_URL antes de inicializar o banco.", file=sys.stderr)
    else:
        print("ERRO: não foi possível inicializar o schema. Confira os logs acima.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
