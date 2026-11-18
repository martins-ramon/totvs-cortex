"""Backfill explícito, limitado e retomável: python -m cortex.knowledge_backfill.

Sem threads, cron implícito ou arquivos como estado. O progresso de cada versão
fica em knowledge_jobs; repetir o comando reaproveita esses registros.
"""
import argparse
import json
import os
from datetime import date

from sqlalchemy import text

from .database import session_factory
from . import knowledge


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use uma data YYYY-MM-DD.') from exc


def _bounded(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError('Informe um limite entre 1 e 100.')
    return number


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('all', 'one_on_one', 'meeting'), default='all')
    parser.add_argument('--after-id', type=int, default=0, help='Cursor por tipo de fonte; use --kind para retomar um tipo.')
    parser.add_argument('--date-from', type=_date)
    parser.add_argument('--date-to', type=_date)
    parser.add_argument('--limit', type=_bounded, default=20, help='Máximo de fontes a inspecionar neste lote (até 100).')
    parser.add_argument('--max-calls', type=_bounded, default=5, help='Máximo de passos de processamento; limita chamadas de IA.')
    parser.add_argument('--user-id', type=int, help='Autor administrativo da execução; validado no cadastro users.')
    parser.add_argument('--apply', action='store_true', help='Enfileirar e processar; sem esta opção é somente simulação.')
    return parser


def run(args):
    if args.after_id < 0:
        raise ValueError('O cursor não pode ser negativo.')
    if args.date_from and args.date_to and args.date_from > args.date_to:
        raise ValueError('A data inicial deve ser anterior à final.')
    if args.apply and not knowledge.enabled():
        raise ValueError('Ative KNOWLEDGE_ENABLED=1 para aplicar o lote.')
    configured_owner = os.environ.get('KNOWLEDGE_OWNER_USER_ID', '').strip()
    user_id = args.user_id or (int(configured_owner) if configured_owner else None)
    if args.apply and (not user_id or user_id <= 0):
        raise ValueError('Informe --user-id do diretor ou configure KNOWLEDGE_OWNER_USER_ID.')
    if configured_owner and user_id is not None and user_id != int(configured_owner):
        raise ValueError('O usuário deve corresponder a KNOWLEDGE_OWNER_USER_ID.')
    db = session_factory()
    try:
        if args.apply and db.execute(text('SELECT id FROM users WHERE id = :id'), {'id': user_id}).first() is None:
            raise ValueError('Usuário administrativo não encontrado.')
        params = {'after': args.after_id, 'limit': args.limit}
        extra = ''
        for name, comparison, value in (('date_from', '>=', args.date_from), ('date_to', '<=', args.date_to)):
            if value:
                params[name] = value
                extra += f' AND occurred_on {comparison} :{name}'
        queries = []
        for kind, table in (('one_on_one', 'one_on_ones'), ('meeting', 'meetings')):
            if args.kind not in ('all', kind):
                continue
            # Os nomes vêm de uma lista fixa; nenhum texto do CLI vira SQL.
            queries.append(f"SELECT '{kind}' AS kind, id, occurred_on FROM {table} "
                           f"WHERE id > :after AND (COALESCE(transcript_raw, '') <> ''" +
                           (" OR COALESCE(private_notes, '') <> '' OR COALESCE(public_notes, '') <> ''" if kind == 'one_on_one'
                            else " OR COALESCE(notes, '') <> ''") + ')' + extra)
        sources = [dict(row) for row in db.execute(text(' UNION ALL '.join(queries) +
                                                      ' ORDER BY kind,id LIMIT :limit'), params).mappings().all()]
        summary = {'dry_run': not args.apply, 'sources': sources,
                   'next_cursors': {kind: max((row['id'] for row in sources if row['kind'] == kind), default=args.after_id)
                                    for kind in ('one_on_one', 'meeting')},
                   'limit': args.limit, 'max_calls': args.max_calls, 'processed': []}
        if not args.apply:
            return summary
        for source in sources:
            knowledge.enqueue_source(db, source['kind'], source['id'], user_id)
        db.commit()
        # Trabalhos de lotes anteriores também podem ser retomados. Jobs com
        # lease vigente continuam pertencendo à requisição que os iniciou.
        kinds = "AND s.kind = :kind" if args.kind != 'all' else ''
        job_params = {'kind': args.kind, 'limit': args.max_calls, 'after': args.after_id}
        job_scope = 'AND s.record_id > :after'
        for name, comparison, value in (('date_from', '>=', args.date_from), ('date_to', '<=', args.date_to)):
            if value:
                job_scope += f' AND v.occurred_on {comparison} :{name}'
                job_params[name] = value
        job_ids = [row[0] for row in db.execute(text(f'''
            SELECT j.id FROM knowledge_jobs j JOIN knowledge_sources s ON s.id = j.source_id
            JOIN knowledge_versions v ON v.id = j.version_id
            WHERE s.deleted = FALSE AND s.current_version_id = j.version_id
              AND j.attempts < 3
              AND (j.status IN ('pending','error') OR
                   (j.status = 'running' AND j.lease_until < CURRENT_TIMESTAMP))
              {kinds} {job_scope} ORDER BY j.updated_at,j.id LIMIT :limit
        '''), job_params).all()]
        db.rollback()  # Nenhuma transação de leitura fica aberta durante a IA.
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for job_id in job_ids:
        result = knowledge.process_job(job_id, user_id)
        summary['processed'].append({'job_id': job_id, 'result': result})
    return summary


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == '__main__':
    main()
