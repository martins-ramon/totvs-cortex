"""Migração dos dados legados do FeedbackAI para o novo schema Cortex.

Idempotente: pode ser executada várias vezes sem duplicar registros.

O que faz:
1. Cria `people` a partir de `users`, exceto os e-mails do diretor
   (DIRECTOR_EMAILS ou ALLOWED_EMAILS) — eles permanecem apenas em `users`.
2. Copia `meetings` -> `one_on_ones` (source='legacy_meeting').
3. Copia `feedbacks` -> `one_on_ones` (source='legacy_feedback'):
   - description (visão do gestor)      -> summary_ai
   - feedback_for_employee              -> public_notes
   - transcription                      -> transcript_raw

As tabelas legadas NÃO são alteradas — preservação total do histórico.

Uso:  python -m cortex.migrate_data
"""
import sys

from sqlalchemy import text

from .database import database_url, session_factory


def run():
    from .security import director_emails
    directors = {email.strip().lower() for email in director_emails()}
    print(f"E-mails tratados como diretor (não viram 'people'): {directors or '(nenhum configurado)'}")

    db = session_factory()
    try:
        # --- 1. users -> people ---
        users = db.execute(text("""
            SELECT id, name, email, company, phone, profile_photo, created_at
            FROM users ORDER BY id
        """)).fetchall()

        created_people = skipped_directors = already_people = 0
        for uid, name, email, company, phone, photo, created_at in users:
            if email and email.strip().lower() in directors:
                skipped_directors += 1
                continue
            exists = db.execute(
                text("SELECT 1 FROM people WHERE legacy_user_id = :uid"),
                {"uid": uid}).fetchone()
            if exists:
                already_people += 1
                continue
            note = f"Empresa (legado): {company}" if company else None
            if phone:
                note = f"{note}\nTelefone (legado): {phone}" if note else f"Telefone (legado): {phone}"
            db.execute(text("""
                INSERT INTO people (full_name, email, photo, notes, legacy_user_id, created_at)
                VALUES (:name, :email, :photo, :note, :uid, COALESCE(:created, CURRENT_TIMESTAMP))
                ON CONFLICT (legacy_user_id) DO NOTHING
            """), {
                "name": name,
                "email": email,
                "photo": photo,
                "note": note,
                "uid": uid,
                "created": created_at,
            })
            created_people += 1

        # --- 2. meetings -> one_on_ones ---
        res_meetings = db.execute(text("""
            INSERT INTO one_on_ones
                (person_id, occurred_on, title, source, transcript_raw, summary_ai, legacy_table, legacy_id)
            SELECT p.id, m.meeting_date, 'Reunião (legado)', 'legacy_meeting',
                   m.transcription, m.summary, 'meetings', m.id
            FROM meetings m
            JOIN people p ON p.legacy_user_id = m.user_id
            WHERE NOT EXISTS (
                SELECT 1 FROM one_on_ones o
                WHERE o.legacy_table = 'meetings' AND o.legacy_id = m.id
            )
        """))
        migrated_meetings = res_meetings.rowcount or 0

        orphan_meetings = db.execute(text("""
            SELECT COUNT(*) FROM meetings m
            WHERE NOT EXISTS (SELECT 1 FROM people p WHERE p.legacy_user_id = m.user_id)
        """)).scalar() or 0

        # --- 3. feedbacks -> one_on_ones ---
        res_feedbacks = db.execute(text("""
            INSERT INTO one_on_ones
                (person_id, occurred_on, title, source, transcript_raw, summary_ai, public_notes, legacy_table, legacy_id)
            SELECT p.id, f.feedback_date, 'Feedback (legado)', 'legacy_feedback',
                   f.transcription,
                   NULLIF(f.description, ''),
                   NULLIF(f.feedback_for_employee, ''),
                   'feedbacks', f.id
            FROM feedbacks f
            JOIN people p ON p.legacy_user_id = f.employee_id
            WHERE NOT EXISTS (
                SELECT 1 FROM one_on_ones o
                WHERE o.legacy_table = 'feedbacks' AND o.legacy_id = f.id
            )
        """))
        migrated_feedbacks = res_feedbacks.rowcount or 0

        orphan_feedbacks = db.execute(text("""
            SELECT COUNT(*) FROM feedbacks f
            WHERE NOT EXISTS (SELECT 1 FROM people p WHERE p.legacy_user_id = f.employee_id)
        """)).scalar() or 0

        db.commit()

        print("--- Migração concluída ---")
        print(f"Pessoas criadas: {created_people} "
              f"(já existentes: {already_people}, diretores pulados: {skipped_directors})")
        print(f"Reuniões migradas para 1:1: {migrated_meetings} "
              f"(sem vínculo com pessoa: {orphan_meetings})")
        print(f"Feedbacks migrados para 1:1: {migrated_feedbacks} "
              f"(sem vínculo com pessoa: {orphan_feedbacks})")
        print("Tabelas legadas preservadas sem alterações.")
    finally:
        db.close()


def main():
    if not database_url():
        print("ERRO: DATABASE_URL não configurada.", file=sys.stderr)
        sys.exit(1)
    run()


if __name__ == "__main__":
    main()
