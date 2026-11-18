"""Projeção Markdown estável, sem segunda interpretação por IA."""
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from posixpath import relpath

from sqlalchemy import text

from .knowledge_query import (_ACTIVE, _COLUMNS, _JOINS, _claim, knowledge_revision,
                              list_tasks, serial)


def _escape(value):
    return re.sub(r'([\\`*{}\[\]<>()#!|_])', r'\\\1', str(value or '')).replace('\r', '').replace('\n', ' ')


def _yaml(meta):
    # JSON scalars/arrays are valid YAML, including hostile quotes/newlines.
    return '---\n' + ''.join(k + ': ' + json.dumps(v, ensure_ascii=False, default=str) + '\n'
                              for k, v in meta.items()) + '---\n\n'


def _code(value):
    value = str(value or '')
    longest = max((len(v) for v in re.findall(r'`+', value)), default=0)
    fence = '`' * max(3, longest + 1)
    return fence + '\n' + value + '\n' + fence + '\n'


def export_vault(db):
    revision = knowledge_revision(db)
    generated = datetime.now(timezone.utc).isoformat()
    files, links = {}, []
    entities = {r['id']: serial(r) for r in db.execute(text('SELECT * FROM knowledge_entities ORDER BY id')).mappings().all()}
    claims = [_claim(r) for r in db.execute(text('SELECT ' + _COLUMNS + ' ' + _JOINS + ' WHERE ' + _ACTIVE + ' ORDER BY c.id')).mappings().all()]
    claim_paths = {c['id']: ('decisions' if c['classification'] == 'decision' else 'claims') + f"/claim-{c['id']}.md" for c in claims}
    tasks, offset = [], 0
    while True:
        page = list_tasks(db, status=None, limit=200, offset=offset)
        tasks.extend(page['tasks'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    relations = [serial(r) for r in db.execute(text("""SELECT r.* FROM knowledge_relations r
        LEFT JOIN knowledge_claims c ON c.id=r.claim_id LEFT JOIN knowledge_sources s ON s.id=c.source_id
        WHERE r.active AND r.review_state<>'rejected' AND (r.claim_id IS NULL OR (""" + _ACTIVE + ')) ORDER BY r.id')).mappings().all()]

    def link(current, target, label):
        links.append((current, target))
        return '[' + _escape(label) + '](' + relpath(target, current.rsplit('/', 1)[0] if '/' in current else '.') + ')'

    def note(path, identity, kind, title, content, **meta):
        files[path] = (_yaml({'id': identity, 'type': kind, 'generated': True,
                             'projection_version': 1, 'knowledge_revision': revision,
                             'generated_at': generated, **meta}) + '# ' + _escape(title) + '\n\n' + content)

    for eid, e in entities.items():
        path = f'entities/entity-{eid}.md'
        lines = ['Identidade canônica: ' + _escape(e['entity_key']),
                 '\nAliases: ' + ', '.join(_escape(a) for a in e['aliases']), '\n## Conhecimento ativo\n']
        for c in claims:
            if eid in set(c['entity_ids'] or []) | {c['speaker_entity_id'], c['subject_entity_id'], c['owner_entity_id']}:
                lines.append('- ' + link(path, claim_paths[c['id']], c['content']))
        lines.append('\n## Relações ativas\n')
        for r in relations:
            if eid in (r['from_entity_id'], r['to_entity_id']):
                other = r['to_entity_id'] if eid == r['from_entity_id'] else r['from_entity_id']
                if other in entities:
                    lines.append('- ' + _escape(r['relation_type']) + ': ' + link(path, f'entities/entity-{other}.md', entities[other]['label']))
        for t in tasks:
            if e['person_id'] and t['person_id'] == e['person_id']:
                lines.append('- ' + link(path, f"commitments/{t['kind']}-{t['id']}.md", t['description']))
        note(path, f'entity:{eid}', e['kind'], e['label'], '\n'.join(lines), version=e['version'], person_id=e['person_id'])

    versions = {}
    for c in claims:
        path = claim_paths[c['id']]
        lines = [_escape(c['content']), '\nClassificação: ' + c['classification'],
                 '\nRevisão: ' + c['review_state'], '\nEstado: ' + c['status'],
                 '\nPrazo atual: ' + str(c['due_date'] or 'não definido'),
                 '\nOrigem: ' + ('anotação humana' if c['origin'] == 'manual' else 'extração automática'),
                 '\nCampos com correção humana: ' + ', '.join(_escape(k) for k in c['human_fields'])]
        for role, label in (('speaker_entity_id', 'Autor da fala'), ('subject_entity_id', 'Sujeito'), ('owner_entity_id', 'Responsável')):
            eid = c[role]
            if eid in entities:
                lines.append('\n' + label + ': ' + link(path, f'entities/entity-{eid}.md', entities[eid]['label']))
        if c['version_id']:
            versions.setdefault(c['version_id'], []).append(c)
            lines.append('\n## Evidência original\n\n' + link(path, f"sources/version-{c['version_id']}.md", f"Fonte {c['source_ref']} · posições {c['evidence_start']}–{c['evidence_end']}"))
        else:
            lines.append('\nProveniência humana própria; esta observação não é uma fala da transcrição.')
        if c.get('commitment_id') or c.get('meeting_action_id'):
            kind = 'commitment' if c.get('commitment_id') else 'meeting_action'
            tid = c.get('commitment_id') or c.get('meeting_action_id')
            lines.append('\nTarefa canônica: ' + link(path, f'commitments/{kind}-{tid}.md', f'{kind}:{tid}'))
        history = [serial(r) for r in db.execute(text("SELECT * FROM knowledge_revisions WHERE record_type='claim' AND record_id=:id ORDER BY id"), {'id': c['id']}).mappings().all()]
        if history:
            lines.append('\n## Histórico de interpretação\n\n' + _code(json.dumps(history, ensure_ascii=False, indent=2)))
        if c.get('conflict'):
            lines.append('\n## Conflito pendente\n\n' + _code(json.dumps(c['conflict'], ensure_ascii=False, indent=2)))
        note(path, f"knowledge:{c['id']}", c['classification'], c['content'], '\n'.join(lines),
             version=c['version'], review_state=c['review_state'], status=c['status'], occurred_on=c['occurred_on'], due_date=c['due_date'])

    for vid, source_claims in versions.items():
        row = db.execute(text('''SELECT v.*,s.kind,s.record_id,s.title FROM knowledge_versions v
            JOIN knowledge_sources s ON s.id=v.source_id WHERE v.id=:id AND NOT s.deleted'''), {'id': vid}).mappings().first()
        path = f'sources/version-{vid}.md'
        lines = ['Trechos originais imutáveis. A evidência sustenta a atribuição da fala, não prova sua veracidade.\n']
        seen = set()
        for c in source_claims:
            positions = (c['evidence_start'], c['evidence_end'])
            if positions in seen:
                continue
            seen.add(positions)
            lines.append(f'## Posições {positions[0]}–{positions[1]}\n\n' + _code(row['source_text'][positions[0]:positions[1]]))
        note(path, f'version:{vid}', 'source', row['title'], '\n'.join(lines),
             source_id=row['source_id'], source_kind=row['kind'], record_id=row['record_id'],
             occurred_on=row['occurred_on'], timezone=row['timezone'], fingerprint=row['fingerprint'])

    for task in tasks:
        path = f"commitments/{task['kind']}-{task['id']}.md"
        history = [serial(r) for r in db.execute(text('SELECT * FROM knowledge_revisions WHERE record_type=:kind AND record_id=:id ORDER BY id'),
            {'kind': task['kind'], 'id': task['id']}).mappings().all()]
        content = (_escape(task['description']) + '\n\nEstado: ' + task['status'] + '\n\nPrazo atual: ' + str(task['due_date'] or 'não definido') +
                   '\n\n## Histórico operacional\n\n' + _code(json.dumps(history, ensure_ascii=False, indent=2)))
        note(path, f"{task['kind']}:{task['id']}", 'commitment', task['description'], content,
             version=task['version'], status=task['status'], due_date=task['due_date'], person_id=task['person_id'])

    note('index.md', 'cortex:memory', 'index', 'Memória do Cortex',
         ('Projeção gerada a partir do Postgres. A aplicação independe do Obsidian. '
          'Anotações humanas são identificadas pela origem e pelo histórico. '
          'Cópias baixadas são independentes e não podem ser revogadas remotamente.\n\n' +
          '\n'.join('- ' + link('index.md', f'entities/entity-{eid}.md', e['label']) for eid, e in entities.items())))
    missing = [(a, b) for a, b in links if b not in files]
    if missing:
        raise ValueError('A projeção possui referências sem destino.')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content)
    return output.getvalue()
