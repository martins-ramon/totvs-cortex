"""Memórias do diretor, com evidência da mensagem e controle de concorrência."""
import hashlib
import unicodedata

from sqlalchemy import text

from .database import session_factory


_COLUMNS = """id, user_id, subject, subject_key, content, evidence,
              source_message, conversation_context, version, created_at, updated_at"""


def _positive_int(value):
    return type(value) is int and value > 0


def _normalize(value):
    return " ".join(value.split())


def _subject_key(subject):
    folded = "".join(char for char in unicodedata.normalize("NFD", subject.casefold())
                     if not unicodedata.combining(char))
    return hashlib.sha256(_normalize(folded).encode("utf-8")).hexdigest()


def _field(proposal, name, limit):
    value = proposal.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Dados da memória inválidos.")
    return value.strip()


def _validate(proposals, message):
    if not isinstance(proposals, list) or len(proposals) > 3:
        raise ValueError("Propostas de memória inválidas.")
    if not isinstance(message, str) or not message.strip() or len(message) > 6000:
        raise ValueError("Mensagem da memória inválida.")
    clean, subjects, ids = [], set(), set()
    normalized_message = _normalize(message)
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise ValueError("Dados da memória inválidos.")
        memory_id = proposal.get("memory_id")
        if memory_id is not None and not _positive_int(memory_id):
            raise ValueError("Memória inválida.")
        subject = _field(proposal, "subject", 160)
        content = _field(proposal, "content", 2400)
        evidence = _field(proposal, "evidence", 2400)
        if _normalize(evidence) not in normalized_message:
            raise ValueError("A evidência da memória não está na mensagem atual.")
        key = _subject_key(subject)
        if key in subjects or (memory_id is not None and memory_id in ids):
            raise ValueError("Memórias duplicadas no mesmo pedido.")
        subjects.add(key)
        ids.add(memory_id)
        clean.append({"memory_id": memory_id, "subject": subject, "subject_key": key,
                      "content": content, "evidence": evidence})
    return clean


def load_memories(user_id):
    """Carrega somente as memórias pertencentes ao diretor autenticado."""
    if not _positive_int(user_id):
        raise ValueError("Usuário inválido.")
    db = session_factory()
    try:
        return [dict(row) for row in db.execute(text(f"""
            SELECT {_COLUMNS} FROM assistant_memories
            WHERE user_id = :uid ORDER BY updated_at DESC, id DESC
        """), {"uid": user_id}).mappings()]
    finally:
        db.close()


def list_memories(user_id):
    return load_memories(user_id)


def _context_versions(context):
    versions = {}
    for source in context.get("sources", []) if isinstance(context, dict) else []:
        if not isinstance(source, dict) or source.get("type") != "memory":
            continue
        source_id = source.get("id")
        version = source.get("memory_version")
        if isinstance(source_id, str) and _positive_int(version):
            versions[source_id] = version
    return versions


def _previous_user_message(history):
    for turn in reversed(history if isinstance(history, list) else []):
        if isinstance(turn, dict) and turn.get("role") == "user":
            content = turn.get("content")
            if isinstance(content, str):
                return content[:6000]
    return ""


def save_memories(user_id, proposals, message, history, context):
    """Salva um lote atômico; correções exigem a versão consultada da memória."""
    if not _positive_int(user_id):
        raise ValueError("Usuário inválido.")
    clean = _validate(proposals, message)
    if not clean:
        return []
    versions = _context_versions(context)
    db = session_factory()
    results = []
    try:
        for proposal in clean:
            memory_id = proposal["memory_id"]
            params = {**proposal, "uid": user_id, "source_message": message.strip(),
                      "conversation_context": _previous_user_message(history)}
            if memory_id is not None and f"memory:{memory_id}" not in versions:
                raise ValueError("A memória não foi consultada nesta conversa.")
            if memory_id is not None:
                current = db.execute(text(f"""
                    SELECT {_COLUMNS} FROM assistant_memories
                    WHERE user_id = :uid AND id = :memory_id
                """), params).mappings().first()
                if current is None:
                    raise ValueError("A memória já não está disponível.")
            else:
                current = db.execute(text(f"""
                    SELECT {_COLUMNS} FROM assistant_memories
                    WHERE user_id = :uid AND subject_key = :subject_key
                """), params).mappings().first()
            if current is None:
                inserted = db.execute(text("""
                    INSERT INTO assistant_memories
                        (user_id, subject, subject_key, content, evidence, source_message, conversation_context)
                    VALUES (:uid, :subject, :subject_key, :content, :evidence, :source_message, :conversation_context)
                    ON CONFLICT (user_id, subject_key) DO NOTHING RETURNING id
                """), params).first()
                if inserted is not None:
                    results.append({"id": inserted[0], "subject": proposal["subject"],
                                    "content": proposal["content"], "status": "saved"})
                    continue
                # Uma inserção concorrente pode ter criado o mesmo assunto.
                current = db.execute(text(f"""
                    SELECT {_COLUMNS} FROM assistant_memories
                    WHERE user_id = :uid AND subject_key = :subject_key
                """), params).mappings().first()
                if current is None:
                    raise ValueError("A memória mudou durante o pedido. Tente novamente.")
            if _normalize(current["content"]) == _normalize(proposal["content"]):
                results.append({"id": current["id"], "subject": current["subject"],
                                "content": current["content"], "status": "unchanged"})
                continue
            version = versions.get(f"memory:{current['id']}")
            if version != current["version"]:
                raise ValueError("A memória mudou ou não foi consultada. Faça o pedido novamente.")
            params.update(memory_id=current["id"], expected_version=version)
            # Renomear um assunto não pode sobrescrever outra memória.
            duplicate = db.execute(text("""
                SELECT id FROM assistant_memories WHERE user_id = :uid
                    AND subject_key = :subject_key AND id <> :memory_id
            """), params).first()
            if duplicate is not None:
                raise ValueError("Já existe uma memória sobre esse assunto.")
            updated = db.execute(text("""
                UPDATE assistant_memories SET subject = :subject, subject_key = :subject_key,
                    content = :content, evidence = :evidence, source_message = :source_message,
                    conversation_context = :conversation_context, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = :uid AND id = :memory_id AND version = :expected_version
                RETURNING id
            """), params).first()
            if updated is None:
                raise ValueError("A memória mudou durante o pedido. Tente novamente.")
            results.append({"id": current["id"], "subject": proposal["subject"],
                            "content": proposal["content"], "status": "updated"})
        db.commit()
        return results
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_memory(user_id, memory_id):
    if not _positive_int(user_id) or not _positive_int(memory_id):
        raise ValueError("Memória inválida.")
    db = session_factory()
    try:
        deleted = db.execute(text("""
            DELETE FROM assistant_memories WHERE user_id = :uid AND id = :mid RETURNING id
        """), {"uid": user_id, "mid": memory_id}).first()
        db.commit()
        return deleted is not None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
