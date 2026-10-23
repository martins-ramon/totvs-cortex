"""Evidence retrieval over the complete Cortex history, with bounded excerpts.

Every database record is examined before selecting excerpts. Retrieval has no
recent-only window: old transcripts and text deep inside long documents remain
searchable. Coverage is returned with the evidence so the model cannot present
a bounded selection as an exhaustive review.
"""
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict

from sqlalchemy import text

from cortex.assistant_gmail import search_gmail
from cortex.database import session_factory
from cortex.meeting_insights import analysis_stale
from cortex.transcripts import normalize_transcript

_CONTEXT_BUDGET = 70_000
_SOURCE_BUDGET = 3_600
_CHUNK_SIZE = 1_400
_CHUNK_OVERLAP = 180
_MAX_SOURCES = 70
_SOURCE_ID = re.compile(r"(?<![A-Za-z0-9_])(?:(?:person|one_on_one|commitment|checkpoint|meeting|meeting_action):[1-9][0-9]{0,9}|email:[A-Za-z0-9_-]{1,200})(?![A-Za-z0-9_:-])")
_FOLLOWUP_REFERENCES = set("""
esse essa esses essas isso isto aquele aquela aqueles aquelas deles delas dele dela
tabela lista grafico organograma linha linhas item itens primeiro primeira segundo
segunda terceiro terceira ultimo ultima anteriores anterior acima abaixo
""".split())
_FOLLOWUP_LANGUAGE = _FOLLOWUP_REFERENCES | set("""
conclua concluir concluido concluidos concluida marque marcar feito feitos feita
cancele cancelar cancelado cancelada crie criar adicione adicionar atualize atualizar
salve salvar registre registrar altere alterar detalhe detalhar aprofunde aprofundar
explique explicar mostre mostrar remova remover abra abrir resolva resolver finalize
finalizar prazo data amanha hoje ontem proxima proximo semana mes dia dias semanas
aberto aberta abertos abertas pendente pendentes observacao descricao descricao
passo passos proximos proximas favor combinado combinados compromisso compromissos
""".split())
_FOLLOWUP_ACTIONS = {"conclua", "concluir", "marque", "marcar", "cancele", "cancelar",
                     "crie", "criar", "adicione", "adicionar", "atualize", "atualizar",
                     "salve", "salvar", "registre", "registrar", "finalize", "finalizar"}
_STOPWORDS = set("""
a ao aos aquela aquele aquelas aqueles aqui as ate atraves cada com como contra
da das de dela dele deles demais dentro depois dessa desse desta deste do dos e
ela ele eles em entre era essa esse esta este eu foi foram ha isso isto ja la
mais mas me meu minha muito na nas nao nem no nos nossa nosso num numa o os ou
para pela pelas pelo pelos por porque qual quais quando que quem se sem seu seus
sim so sobre sua suas tambem tem temos tenho ter toda todas todo todos tudo um
sao sendo estao estou esteja estavam seria foram
uma umas uns voce voces vou quero gostaria favor pode podem poderia consegue
responda responder diga mostre mostrar conte explique explicar faca fazer partir
principal principais ponto pontos atencao levantado levantados indicada indicado
realizado realizados realizado reuniao reunioes transcricao transcricoes historico
combinado combinados checkpoint checkpoints 1 1s 1on1 1on1s
""".split())


def _fold(value):
    return "".join(c for c in unicodedata.normalize("NFD", str(value or "").casefold())
                   if not unicodedata.combining(c))


def _terms(value):
    return list(dict.fromkeys(word for word in re.findall(r"[a-z0-9]+", _fold(value))
                             if len(word) > 2 and word not in _STOPWORDS))


def _json(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return default if value is None else value


def _string(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value) if value else None


def _contains(needle, haystack):
    return bool(needle and re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack))


def _match_people(message, history, people):
    def find(value):
        folded = " ".join(_fold(value).split())
        claims = defaultdict(set)
        for person in people:
            full_name = person["full_name"] or ""
            aliases = [full_name, person.get("preferred_name") or "",
                       person.get("email") or "", *person.get("transcript_aliases", [])]
            aliases.extend(part for part in _terms(full_name) if len(part) >= 3)
            for alias in aliases:
                normalized = " ".join(_fold(alias).split())
                if normalized:
                    claims[normalized].add(person["id"])
        occurrences = []
        for name, ids in claims.items():
            for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", folded):
                occurrences.append((match.start(), match.end(), ids))
        exact, ambiguous = set(), set()
        for start, end, ids in occurrences:
            # A complete name disambiguates an included first name or alias.
            if any(a <= start and b >= end and b - a > end - start for a, b, _ in occurrences):
                continue
            exact.update(ids)
            if len(ids) > 1:
                ambiguous.update(ids)
        return exact, ambiguous

    matched, ambiguous = find(message)
    followup = bool(re.search(r"\b(ele|ela|dele|dela|deles|delas|isso|esses|essas|continue|detalhe)\b", _fold(message)))
    followup = followup or (_fold(message).strip().startswith("e ") and len(_terms(message)) <= 2)
    if not matched and followup:
        # Only user-authored turns resolve pronouns/follow-ups; assistant output
        # can mention many other people and is not authoritative scope.
        for entry in reversed((history or [])[-8:]):
            if isinstance(entry, dict) and entry.get("role") == "user":
                previous, previous_ambiguous = find(entry.get("content") or entry.get("message") or "")
                if previous:
                    return previous, previous_ambiguous
    return matched, ambiguous


def _record(kind, row, person, body, date=None, title=None):
    person_name = (person or {}).get("full_name") or "Pessoa não cadastrada"
    labels = {"person": "Perfil", "one_on_one": "1:1", "commitment": "Combinado", "checkpoint": "Checkpoint"}
    return {"id": f"{kind}:{row['id']}", "type": kind,
            "title": title or f"{labels[kind]} · {person_name}",
            "excerpt": body, "date": _iso(date),
            "person_id": row["id"] if kind == "person" else row.get("person_id"),
            "record_id": row["id"]}


def _record_people(record):
    """Relevance comes from declared attendance or an explicit action assignment."""
    ids = {record["person_id"]} if record.get("person_id") is not None else set()
    if record["type"] == "meeting":
        ids.update(record.get("participant_ids") or [])
        ids.update(record.get("assigned_person_ids") or [])
    return ids


def _meeting_excerpt(row, participants, people):
    """Keep evidence readable while source IDs/analysis metadata stay separate."""
    stale = analysis_stale(row, participants)
    lines = ["Reunião coletiva: participantes declarados não são necessariamente autores das falas.",
             "Participantes: " + ("; ".join(p["full_name"] for p in participants) or "não informados")]
    for key, label in (("title", "Título"), ("forum", "Fórum"),
                       ("external_participants", "Convidados")):
        if row.get(key):
            lines.append(f"{label}: {row[key]}")
    if stale:
        lines.append("Análise anterior desatualizada omitida; utilize as notas e a transcrição atuais.")
    for key, label in (("notes", "Anotações"), ("transcript_raw", "Transcrição")):
        if not row.get(key):
            continue
        normalized = normalize_transcript(row[key], people)
        if normalized["corrections"]:
            lines.append("Nomes corrigidos com grafias cadastradas; original preservado.")
        lines.append(f"{label}:\n{normalized['text']}")
    if stale:
        return "\n".join(lines)
    extraction = _json(row.get("extraction_json"), {})
    extraction = extraction if isinstance(extraction, dict) else {}
    summary = row.get("summary_ai") or extraction.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.append(f"Resumo:\n{summary}")
    for key, label in (("topics", "Temas"), ("decisions", "Decisões"),
                       ("risks", "Riscos"), ("opportunities", "Oportunidades"),
                       ("strategic_themes", "Temas estratégicos"),
                       ("open_questions", "Questões em aberto")):
        values = extraction.get(key)
        if key == "topics":
            stored = _json(row.get("topics"), [])
            values = (stored if isinstance(stored, list) else []) + (values if isinstance(values, list) else [])
        if isinstance(values, list):
            values = list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
            if values:
                lines.append(label + ":\n" + "\n".join("- " + v for v in values))
    actions = extraction.get("actions")
    suggestions = []
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict) or not isinstance(action.get("description"), str):
            continue
        description = action["description"].strip()
        if not description:
            continue
        owner = action.get("owner_name")
        owner = owner.strip() if isinstance(owner, str) and owner.strip() else "não definido"
        due = _iso(action.get("due_date")) or "não definido"
        suggestions.append(f"- {description} — Responsável: {owner}; prazo: {due}")
    if suggestions:
        lines.append("Encaminhamentos sugeridos pela análise (adoção ainda não comprovada):\n" + "\n".join(suggestions))
    return "\n".join(lines)


def _meeting_action_excerpt(row, person):
    statuses = {"open": "em aberto", "done": "concluído", "cancelled": "cancelado"}
    owner = (person or {}).get("full_name") or row.get("owner_name") or "não definido"
    lines = [f"Encaminhamento: {row['description']}", f"Responsável: {owner}",
             f"Status: {statuses.get(row.get('status'), 'não informado')}",
             f"Reunião: {row['meeting_title']}"]
    for key, label in (("forum", "Fórum"), ("due_date", "Prazo"), ("closed_at", "Encerrado em")):
        if row.get(key):
            lines.append(f"{label}: {_iso(row[key])}")
    return "\n".join(lines)


def _conversation_hints(message, history, records, explicit_person_ids):
    """Resolve UI references to current records, never to old answer text.

    Rich replies include their source IDs in assistant history. They are only
    retrieval hints for short references such as "conclua o primeiro da tabela".
    The evidence is always reloaded from the database (or this Gmail scan).
    """
    terms = set(_terms(message))
    normalized = _fold(message)
    if len(message) > 320 or len(terms) > 24:
        return []
    references_ui = any(_contains(word, normalized) for word in _FOLLOWUP_REFERENCES)
    short_action = bool(terms & _FOLLOWUP_ACTIONS) and terms.issubset(_FOLLOWUP_LANGUAGE)
    if not references_ui and not short_action:
        return []
    assistant_index, assistant_content = None, ""
    entries = (history or [])[-8:]
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if isinstance(entry, dict) and entry.get("role") == "assistant":
            assistant_index = index
            assistant_content = str(entry.get("content") or "")[:24_000]
            break
    if assistant_index is None:
        return []
    prior_question = ""
    for entry in reversed(entries[:assistant_index]):
        if isinstance(entry, dict) and entry.get("role") == "user":
            prior_question = str(entry.get("content") or "")[:4_000]
            break
    prior_text = _fold(assistant_content + " " + prior_question)
    # A new topic such as "crie um organograma do Alestan" must not inherit
    # unrelated source IDs just because "organograma" is a rich UI noun.
    new_terms = terms - _FOLLOWUP_LANGUAGE
    if any(not _contains(term, prior_text) for term in new_terms):
        return []
    known = {record["id"]: record for record in records}
    hints = []
    for match in _SOURCE_ID.finditer(assistant_content):
        source_id = match.group()
        if source_id in known and source_id not in hints:
            hints.append(source_id)
            if len(hints) >= _MAX_SOURCES:
                break
    hint_people = {pid for source_id in hints for pid in _record_people(known[source_id])}
    if explicit_person_ids and not explicit_person_ids.issubset(hint_people):
        return []
    return hints


def _load_database():
    db = session_factory()
    try:
        people = [dict(row) for row in db.execute(text("""
            SELECT id, full_name, preferred_name, email, role_title, notes,
                   active, transcript_aliases FROM people ORDER BY id
        """)).mappings().all()]
        for person in people:
            aliases = _json(person.get("transcript_aliases"), [])
            person["transcript_aliases"] = [a for a in aliases if isinstance(a, str)] if isinstance(aliases, list) else []
            person["active"] = bool(person["active"])
        by_id = {person["id"]: person for person in people}
        records = []
        for person in people:
            body = "\n".join(f"{key}: {_string(person.get(key))}" for key in (
                "full_name", "preferred_name", "email", "role_title", "active", "notes", "transcript_aliases"))
            records.append(_record("person", person, person, body))
        # These queries intentionally have neither a date predicate nor LIMIT.
        # Values from the chat are never interpolated into SQL.
        specs = [
            ("one_on_one", """SELECT id, person_id, occurred_on, title, transcript_raw,
                summary_ai, private_notes, public_notes, topics, extraction_json
                FROM one_on_ones ORDER BY occurred_on, id""", "occurred_on",
             ("title", "summary_ai", "private_notes", "public_notes", "topics", "extraction_json", "transcript_raw")),
            ("commitment", """SELECT id, person_id, one_on_one_id, description,
                owner, due_date, status, closed_at, created_at FROM commitments ORDER BY id""", "created_at",
             ("description", "owner", "due_date", "status", "closed_at", "one_on_one_id")),
            ("checkpoint", """SELECT id, person_id, checkpoint_date, period_start,
                period_end, actions_json, private_notes, public_notes, raw_input
                FROM checkpoints ORDER BY checkpoint_date, id""", "checkpoint_date",
             ("period_start", "period_end", "actions_json", "private_notes", "public_notes", "raw_input")),
        ]
        for kind, query, date_key, fields in specs:
            for row in db.execute(text(query)).mappings():
                row = dict(row)
                person = by_id.get(row["person_id"])
                corrected_names = False
                if kind == "one_on_one" and row.get("transcript_raw"):
                    normalized = normalize_transcript(row["transcript_raw"], people)
                    row["transcript_raw"] = normalized["text"]
                    corrected_names = bool(normalized["corrections"])
                body = "\n".join(f"{key}: {_string(row.get(key))}" for key in fields if row.get(key) is not None)
                if corrected_names:
                    body = "Nomes da transcrição corrigidos com as grafias cadastradas; original preservado.\n" + body
                title = None
                if kind == "one_on_one" and row.get("title"):
                    title = f"{row['title']} · {(person or {}).get('full_name', '')}"
                records.append(_record(kind, row, person, body, row.get(date_key), title))
        # A collective meeting is one source, regardless of attendee count.
        # This scans the entire raw history as well as current AI analysis.
        participants_by_meeting = defaultdict(list)
        for participant in db.execute(text("""
            SELECT mp.meeting_id, p.id, p.full_name
            FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
            ORDER BY mp.meeting_id, p.id
        """)).mappings():
            participants_by_meeting[participant["meeting_id"]].append({
                "id": participant["id"], "full_name": participant["full_name"]})
        actions = [dict(raw) for raw in db.execute(text("""
            SELECT a.id, a.meeting_id, a.person_id, a.description, a.owner_name,
                   a.due_date, a.status, a.closed_at, a.created_at,
                   m.title AS meeting_title, m.forum, m.occurred_on
            FROM meeting_actions a JOIN meetings m ON m.id = a.meeting_id
            ORDER BY a.id
        """)).mappings()]
        assignments_by_meeting = defaultdict(set)
        for action in actions:
            if action["person_id"] is not None:
                assignments_by_meeting[action["meeting_id"]].add(action["person_id"])
        for raw in db.execute(text("""
            SELECT id, title, forum, occurred_on, external_participants,
                   transcript_raw, notes, summary_ai, topics, extraction_json
            FROM meetings ORDER BY occurred_on, id
        """)).mappings():
            row = dict(raw)
            participants = participants_by_meeting[row["id"]]
            records.append({"id": f"meeting:{row['id']}", "type": "meeting",
                            "title": f"Reunião · {row['title']}",
                            "excerpt": _meeting_excerpt(row, participants, people),
                            "date": _iso(row["occurred_on"]), "person_id": None,
                            "record_id": row["id"], "meeting_id": row["id"],
                            "participant_ids": [p["id"] for p in participants],
                            "assigned_person_ids": sorted(assignments_by_meeting[row["id"]])})
        for row in actions:
            person = by_id.get(row["person_id"])
            records.append({"id": f"meeting_action:{row['id']}", "type": "meeting_action",
                            "title": f"Encaminhamento · {row['meeting_title']}",
                            "excerpt": _meeting_action_excerpt(row, person),
                            "date": _iso(row["occurred_on"]), "person_id": row["person_id"],
                            "record_id": row["id"], "meeting_id": row["meeting_id"],
                            "participant_ids": []})
        return people, records
    finally:
        db.close()


def _chunks(value):
    # Chunk on original offsets so matching accented text does not move the
    # selected passage. Overlap keeps sentences across boundaries available.
    if not value:
        return []
    return [(start, value[start:start + _CHUNK_SIZE])
            for start in range(0, len(value), _CHUNK_SIZE - _CHUNK_OVERLAP)]


def _score(value, terms, weights):
    normalized = _fold(value)
    return sum(weights.get(term, 1.0) for term in terms if _contains(term, normalized))


def _excerpt(value, terms, weights):
    if len(value) <= _SOURCE_BUDGET:
        return value, False
    chunks = _chunks(value)
    ranked = sorted(chunks, key=lambda item: (-_score(item[1], terms, weights), item[0]))
    # Several matching passages may describe different reporting relationships
    # or decisions. Keep separated evidence rather than a leading prefix.
    selected, used = [], 0
    for start, chunk in ranked:
        if any(abs(start - old_start) < _CHUNK_SIZE - _CHUNK_OVERLAP for old_start, _ in selected):
            continue
        room = _SOURCE_BUDGET - used - 50
        if room < 150:
            break
        chunk = chunk[:room]
        selected.append((start, chunk))
        used += len(chunk) + 50
    return "\n[…]\n".join(f"[trecho na posição {start}]\n{chunk}" for start, chunk in sorted(selected)), True


def _select(records, terms, matched_ids, budget=_CONTEXT_BUDGET, source_hints=None):
    source_hints = set(source_hints or [])
    frequencies = Counter()
    for record in records:
        value = _fold(record["excerpt"] + " " + record["title"])
        frequencies.update(term for term in terms if _contains(term, value))
    weights = {term: 1 + math.log((len(records) + 1) / (count + 1))
               for term, count in frequencies.items()}
    candidates = []
    for record in records:
        score = _score(record["excerpt"] + " " + record["title"], terms, weights)
        person_match = bool(_record_people(record) & matched_ids) or (record["type"] == "email" and bool(matched_ids))
        hinted = record["id"] in source_hints
        if terms and score == 0 and not person_match and not hinted:
            continue
        item = dict(record)
        item["_score"] = score + (4 if person_match else 0) + (100 if hinted else 0)
        item["excerpt"], item["excerpt_partial"] = _excerpt(item["excerpt"], terms, weights)
        candidates.append(item)

    # Round-robin across people, types and calendar years gives older evidence
    # a chance even when a broad question matches hundreds of recent records.
    groups = defaultdict(list)
    for record in candidates:
        year = (record.get("date") or "")[:4]
        groups[(record["person_id"], record["type"], year)].append(record)
    for group in groups.values():
        group.sort(key=lambda record: (-record["_score"], record.get("date") or "", record["id"]))
    ordered_groups = sorted(groups.values(), key=lambda group: -group[0]["_score"])
    ordered = []
    while any(ordered_groups):
        for group in ordered_groups:
            if group:
                ordered.append(group.pop(0))
    selected, chars = [], 0
    for record in ordered:
        if len(selected) >= _MAX_SOURCES:
            break
        # Reserve part of the budget for source metadata and the people list.
        cost = len(record["excerpt"]) + len(record["title"]) + 220
        if chars + cost > budget:
            continue
        chars += cost
        selected.append({key: value for key, value in record.items() if key != "_score"})
    return selected, candidates, chars


def gather_context(user_id, message, history=None):
    """Collect evidence for an authenticated director, safely degrading Gmail."""
    warnings = []
    people, records = _load_database()
    matched_ids, ambiguous_ids = _match_people(message, history, people)
    if ambiguous_ids:
        warnings.append("O nome citado corresponde a mais de uma pessoa. Confirme o nome completo antes de executar pedidos sobre uma pessoa específica.")
    terms = _terms(message)
    if not terms and matched_ids and history:
        for entry in reversed(history[-4:]):
            if isinstance(entry, dict) and entry.get("role") == "user":
                terms = _terms(entry.get("content") or "")
                if terms:
                    break
    selected_people = [person for person in people if person["id"] in matched_ids]
    try:
        mail = search_gmail(user_id, selected_people, terms)
    except Exception:
        mail = {"sources": [], "coverage": {"status": "unavailable", "partial": True},
                "warnings": ["Gmail indisponível nesta consulta; os registros do Cortex continuam disponíveis."]}
    warnings.extend(mail["warnings"])
    all_records = records + mail["sources"]
    explicit_ids, _ = _match_people(message, None, people)
    source_hints = _conversation_hints(message, history, all_records, explicit_ids)
    # Reserve space for the person directory as well as evidence. Notes remain
    # searchable in full through person sources; metadata is only a brief view.
    metadata_base = [{**person, "notes": ""} for person in people]
    base_chars = len(json.dumps(metadata_base, ensure_ascii=False, default=str))
    note_limit = min(1_500, max(0, (_CONTEXT_BUDGET // 5 - base_chars) // max(1, len(people))))
    metadata_all = [{**person, "notes": (person.get("notes") or "")[:note_limit]} for person in people]
    metadata_chars = len(json.dumps(metadata_all, ensure_ascii=False, default=str))
    selected, candidates, chars = _select(all_records, terms, matched_ids,
                                         budget=max(0, _CONTEXT_BUDGET - metadata_chars),
                                         source_hints=source_hints)
    selected_email = [item for item in selected if item["type"] == "email"]
    mail["coverage"]["selected_message_count"] = len(selected_email)
    mail["coverage"]["context_partial"] = (len(selected_email) < len(mail["sources"])
                                             or any(item["excerpt_partial"] for item in selected_email))
    mail["coverage"]["partial"] = mail["coverage"].get("partial", False) or mail["coverage"]["context_partial"]
    counts = lambda items: dict(Counter(item["type"] for item in items))
    dates = sorted(record["date"][:10] for record in records if record.get("date"))
    partial = len(selected) < len(candidates) or any(item["excerpt_partial"] for item in selected)
    if partial:
        warnings.append("O histórico completo foi pesquisado; a resposta usa uma seleção de fontes e trechos por limite de contexto.")
    if not selected:
        warnings.append("Nenhuma fonte com correspondência foi encontrada. Tente informar o nome, uma variação cadastrada ou o assunto exato.")
    # Relevant people are returned in full; other names remain discoverable to
    # support organization charts, without flooding the model with every note.
    people_metadata = []
    included_people = matched_ids | {pid for item in selected for pid in _record_people(item)}
    for person in metadata_all:
        if person["id"] in included_people or not matched_ids:
            people_metadata.append(person)
    chars += len(json.dumps(people_metadata, ensure_ascii=False, default=str))
    truncated_notes = sum(len(person.get("notes") or "") > note_limit for person in people)
    return {"sources": selected, "people": people_metadata,
            "coverage": {
                "database": {"status": "available", "scanned_by_type": counts(records),
                             "matching_by_type": counts(item for item in candidates if item["type"] != "email"),
                             "selected_by_type": counts(item for item in selected if item["type"] != "email"),
                             "date_range": {"from": dates[0] if dates else None, "to": dates[-1] if dates else None},
                             "date_filter": None, "partial": partial,
                             "person_notes_summarized": truncated_notes,
                             "context_chars": chars, "context_char_budget": _CONTEXT_BUDGET},
                "gmail": mail["coverage"], "matched_person_ids": sorted(matched_ids),
                "ambiguous_person_ids": sorted(ambiguous_ids),
                "conversation_source_hints": source_hints},
            "warnings": warnings}
