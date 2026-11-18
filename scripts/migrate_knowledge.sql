-- Cortex: memória estruturada de reuniões.
-- Execute este arquivo inteiro no SQL Editor do Supabase, como proprietário
-- administrativo (normalmente postgres), na base existente do Cortex.
-- Origem: cortex/knowledge_schema.py (KNOWLEDGE_DDL).
-- Migração aditiva e idempotente, em uma única transação.
-- Não executa backfill, chamadas de IA ou alterações imediatas nos registros.
-- Os triggers passam a reconciliar memória/cards quando fontes forem editadas
-- ou excluídas, inclusive com KNOWLEDGE_ENABLED=0.

BEGIN;
SET LOCAL search_path = public, pg_catalog;

-- 1. Verifica a base existente antes de aplicar qualquer mudança.
DO $cortex_knowledge_preflight$
DECLARE
    required_table TEXT;
    missing_tables TEXT[] := ARRAY[]::TEXT[];
BEGIN
    FOREACH required_table IN ARRAY ARRAY[
        'users', 'people', 'one_on_ones', 'commitments',
        'meetings', 'meeting_actions', 'member_cards', 'card_jobs'
    ] LOOP
        IF to_regclass(format('public.%I', required_table)) IS NULL THEN
            missing_tables := array_append(missing_tables, required_table);
        END IF;
    END LOOP;
    IF cardinality(missing_tables) > 0 THEN
        RAISE EXCEPTION 'Base Cortex incompleta. Tabelas ausentes: %', array_to_string(missing_tables, ', ')
            USING HINT = 'Aplique primeiro o schema existente do Cortex. Para as tabelas de fóruns, use scripts/migrate_forum_meetings.sql.';
    END IF;
END;
$cortex_knowledge_preflight$;

-- 2. Compatibilidade com evoluções já usadas pelo Cortex.
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS assistant_request_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_commitments_assistant_request ON commitments (assistant_request_id);
ALTER TABLE people ADD COLUMN IF NOT EXISTS transcript_aliases JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE one_on_ones ADD COLUMN IF NOT EXISTS extraction_json JSONB;
ALTER TABLE card_jobs ADD COLUMN IF NOT EXISTS progress_json JSONB;

-- 3. DDL da memória: tabelas, índices, colunas, RLS, funções e triggers.


-- knowledge_entities
CREATE TABLE IF NOT EXISTS knowledge_entities (
        id BIGSERIAL PRIMARY KEY, entity_key TEXT UNIQUE NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('person','project','topic')),
        label TEXT NOT NULL, person_id INTEGER UNIQUE REFERENCES people(id) ON DELETE CASCADE,
        aliases JSONB NOT NULL DEFAULT '[]'::jsonb, version INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((kind = 'person') = (person_id IS NOT NULL))
    );

-- knowledge_sources
CREATE TABLE IF NOT EXISTS knowledge_sources (
        id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('one_on_one','meeting')),
        record_id INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', current_version_id BIGINT,
        deleted BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(kind,record_id)
    );

-- knowledge_versions
CREATE TABLE IF NOT EXISTS knowledge_versions (
        id BIGSERIAL PRIMARY KEY, source_id BIGINT NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL, source_text TEXT NOT NULL, occurred_on DATE NOT NULL,
        timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(source_id,fingerprint)
    );

-- knowledge_claims
CREATE TABLE IF NOT EXISTS knowledge_claims (
        id BIGSERIAL PRIMARY KEY, source_id BIGINT REFERENCES knowledge_sources(id) ON DELETE CASCADE,
        version_id BIGINT REFERENCES knowledge_versions(id) ON DELETE CASCADE, source_key TEXT,
        evidence_start INTEGER, evidence_end INTEGER, content TEXT NOT NULL,
        classification TEXT NOT NULL CHECK (classification IN
            ('declaration','report','perception','hypothesis','proposal','decision','commitment','question')),
        speaker_entity_id BIGINT REFERENCES knowledge_entities(id) ON DELETE SET NULL,
        subject_entity_id BIGINT REFERENCES knowledge_entities(id) ON DELETE SET NULL,
        owner_entity_id BIGINT REFERENCES knowledge_entities(id) ON DELETE SET NULL,
        entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        review_state TEXT NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending','accepted','corrected','rejected')),
        operational_state TEXT NOT NULL DEFAULT 'none' CHECK (operational_state IN ('none','open','done','cancelled')),
        occurred_on DATE, due_date DATE, due_expression TEXT, condition_text TEXT,
        commitment_id INTEGER REFERENCES commitments(id) ON DELETE SET NULL,
        meeting_action_id INTEGER REFERENCES meeting_actions(id) ON DELETE SET NULL,
        task_accepted BOOLEAN NOT NULL DEFAULT FALSE,
        version INTEGER NOT NULL DEFAULT 1,
        origin TEXT NOT NULL DEFAULT 'automatic' CHECK (origin IN ('automatic','manual')),
        human_fields JSONB NOT NULL DEFAULT '{}'::jsonb, conflict JSONB,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source_id,source_key),
        CHECK ((origin = 'manual') OR (source_id IS NOT NULL AND version_id IS NOT NULL
               AND evidence_start >= 0 AND evidence_end > evidence_start))
    );

-- knowledge_relations
CREATE TABLE IF NOT EXISTS knowledge_relations (
        id BIGSERIAL PRIMARY KEY,
        from_entity_id BIGINT NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
        to_entity_id BIGINT NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
        relation_type TEXT NOT NULL CHECK (relation_type IN
            ('mentions','depends_on','about','responsible_for','decided','related_to')),
        claim_id BIGINT REFERENCES knowledge_claims(id) ON DELETE CASCADE, source_key TEXT,
        review_state TEXT NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending','accepted','corrected','rejected')),
        version INTEGER NOT NULL DEFAULT 1,
        origin TEXT NOT NULL DEFAULT 'automatic' CHECK (origin IN ('automatic','manual')),
        human_fields JSONB NOT NULL DEFAULT '{}'::jsonb, active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(claim_id,from_entity_id,to_entity_id,relation_type)
    );

-- knowledge_revisions
CREATE TABLE IF NOT EXISTS knowledge_revisions (
        id BIGSERIAL PRIMARY KEY, record_type TEXT NOT NULL, record_id BIGINT NOT NULL,
        previous_version INTEGER, actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        origin TEXT NOT NULL, before_json JSONB, after_json JSONB, reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

-- knowledge_jobs
CREATE TABLE IF NOT EXISTS knowledge_jobs (
        id BIGSERIAL PRIMARY KEY, source_id BIGINT NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
        version_id BIGINT NOT NULL UNIQUE REFERENCES knowledge_versions(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','error','superseded')),
        attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until TIMESTAMPTZ,
        cursor_json JSONB NOT NULL DEFAULT '{}'::jsonb, staged_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb, error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

-- knowledge_outbox
CREATE TABLE IF NOT EXISTS knowledge_outbox (
        id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'cards',
        person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done')),
        revision_token TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

-- knowledge_entities.rls
ALTER TABLE knowledge_entities ENABLE ROW LEVEL SECURITY;

-- knowledge_sources.rls
ALTER TABLE knowledge_sources ENABLE ROW LEVEL SECURITY;

-- knowledge_versions.rls
ALTER TABLE knowledge_versions ENABLE ROW LEVEL SECURITY;

-- knowledge_claims.rls
ALTER TABLE knowledge_claims ENABLE ROW LEVEL SECURITY;

-- knowledge_relations.rls
ALTER TABLE knowledge_relations ENABLE ROW LEVEL SECURITY;

-- knowledge_revisions.rls
ALTER TABLE knowledge_revisions ENABLE ROW LEVEL SECURITY;

-- knowledge_jobs.rls
ALTER TABLE knowledge_jobs ENABLE ROW LEVEL SECURITY;

-- knowledge_outbox.rls
ALTER TABLE knowledge_outbox ENABLE ROW LEVEL SECURITY;

-- knowledge_relations.source_key
ALTER TABLE knowledge_relations ADD COLUMN IF NOT EXISTS source_key TEXT;

-- knowledge_claims.condition_text
ALTER TABLE knowledge_claims ADD COLUMN IF NOT EXISTS condition_text TEXT;

-- idx_knowledge_relations_source_key
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_relations_source_key ON knowledge_relations (claim_id,source_key);

-- idx_knowledge_claims_active
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_active
        ON knowledge_claims (active,review_state,occurred_on DESC,id);

-- idx_knowledge_claims_commitment
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_commitment ON knowledge_claims (commitment_id);

-- idx_knowledge_claims_meeting_action
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_meeting_action ON knowledge_claims (meeting_action_id);

-- idx_knowledge_claims_entities
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_entities
        ON knowledge_claims USING GIN (entity_ids);

-- idx_knowledge_claims_search
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_search
        ON knowledge_claims USING GIN (to_tsvector('portuguese',content));

-- idx_knowledge_relations_from
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_from
        ON knowledge_relations (from_entity_id,to_entity_id);

-- idx_knowledge_jobs_pending
CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_pending
        ON knowledge_jobs (status,updated_at,id);

-- idx_knowledge_revisions_record
CREATE INDEX IF NOT EXISTS idx_knowledge_revisions_record
        ON knowledge_revisions (record_type,record_id,id);

-- idx_knowledge_outbox_pending
CREATE INDEX IF NOT EXISTS idx_knowledge_outbox_pending
        ON knowledge_outbox (person_id,status,id);

-- commitments.knowledge_version
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS knowledge_version INTEGER NOT NULL DEFAULT 1;

-- commitments.knowledge_human_fields
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS knowledge_human_fields JSONB NOT NULL DEFAULT '{}'::jsonb;

-- commitments.knowledge_event_on
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS knowledge_event_on DATE;

-- commitments.knowledge_created
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS knowledge_created BOOLEAN NOT NULL DEFAULT FALSE;

-- meeting_actions.knowledge_version
ALTER TABLE meeting_actions ADD COLUMN IF NOT EXISTS knowledge_version INTEGER NOT NULL DEFAULT 1;

-- meeting_actions.knowledge_human_fields
ALTER TABLE meeting_actions ADD COLUMN IF NOT EXISTS knowledge_human_fields JSONB NOT NULL DEFAULT '{}'::jsonb;

-- meeting_actions.knowledge_event_on
ALTER TABLE meeting_actions ADD COLUMN IF NOT EXISTS knowledge_event_on DATE;

-- meeting_actions.knowledge_created
ALTER TABLE meeting_actions ADD COLUMN IF NOT EXISTS knowledge_created BOOLEAN NOT NULL DEFAULT FALSE;

-- member_cards.knowledge_revision
ALTER TABLE member_cards ADD COLUMN IF NOT EXISTS knowledge_revision TEXT;

-- cortex_knowledge_flag_removed_support
CREATE OR REPLACE FUNCTION cortex_knowledge_flag_removed_support(sid BIGINT)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE task_ref RECORD; task_data JSONB; remaining_ids JSONB; fields TEXT[]; warning JSONB;
BEGIN
    FOR task_ref IN
        SELECT 'commitment' AS kind,commitment_id AS id FROM knowledge_claims
            WHERE source_id=sid AND commitment_id IS NOT NULL
        UNION SELECT 'meeting_action',meeting_action_id FROM knowledge_claims
            WHERE source_id=sid AND meeting_action_id IS NOT NULL
    LOOP
        IF task_ref.kind='commitment' THEN
            SELECT to_jsonb(t) INTO task_data FROM commitments t WHERE id=task_ref.id FOR UPDATE;
            SELECT jsonb_agg(c.id ORDER BY c.id) INTO remaining_ids FROM knowledge_claims c
                LEFT JOIN knowledge_sources s ON s.id=c.source_id
                WHERE c.commitment_id=task_ref.id AND c.source_id IS DISTINCT FROM sid AND c.active
                    AND c.review_state<>'rejected' AND c.classification='commitment' AND c.task_accepted
                    AND (c.source_id IS NULL OR NOT s.deleted);
        ELSE
            SELECT to_jsonb(t) INTO task_data FROM meeting_actions t WHERE id=task_ref.id FOR UPDATE;
            SELECT jsonb_agg(c.id ORDER BY c.id) INTO remaining_ids FROM knowledge_claims c
                LEFT JOIN knowledge_sources s ON s.id=c.source_id
                WHERE c.meeting_action_id=task_ref.id AND c.source_id IS DISTINCT FROM sid AND c.active
                    AND c.review_state<>'rejected' AND c.classification='commitment' AND c.task_accepted
                    AND (c.source_id IS NULL OR NOT s.deleted);
        END IF;
        IF task_data IS NULL OR remaining_ids IS NULL OR NOT EXISTS (
            SELECT 1 FROM knowledge_versions v WHERE v.source_id=sid
                AND v.occurred_on::text=task_data->>'knowledge_event_on') THEN CONTINUE; END IF;
        fields := ARRAY[]::TEXT[];
        IF task_data->>'due_date' IS NOT NULL AND NOT (task_data->'knowledge_human_fields' ? 'due_date') THEN
            fields := array_append(fields,'due_date');
        END IF;
        IF NOT (task_data->'knowledge_human_fields' ? 'status') THEN fields := array_append(fields,'status'); END IF;
        IF cardinality(fields)=0 THEN CONTINUE; END IF;
        warning := jsonb_build_object('kind',task_ref.kind,'id',task_ref.id,'fields',to_jsonb(fields),
            'reason','Origem do estado atual foi removida; confirmar prazo/estado antes de usar',
            'current',jsonb_build_object('due_date',task_data->'due_date','status',task_data->'status'),
            'removed_source_id',sid,'remaining_claim_ids',remaining_ids);
        UPDATE knowledge_claims c SET conflict=(CASE WHEN jsonb_typeof(c.conflict)='object' THEN c.conflict ELSE '{}'::jsonb END)
            ||jsonb_build_object('task_evidence_removed',warning),version=c.version+1,updated_at=CURRENT_TIMESTAMP
            WHERE c.id IN (SELECT jsonb_array_elements_text(remaining_ids)::BIGINT);
    END LOOP;
END $$;

-- cortex_knowledge_preserve_actions
CREATE OR REPLACE FUNCTION cortex_knowledge_preserve_actions(sid BIGINT,mid INTEGER)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE action_record RECORD; alternative_meeting INTEGER; alternative_session INTEGER;
        migrated_id INTEGER; migrated_record JSONB;
BEGIN
    FOR action_record IN SELECT * FROM meeting_actions WHERE meeting_id=mid FOR UPDATE LOOP
        SELECT s.record_id INTO alternative_meeting FROM knowledge_claims c
            JOIN knowledge_sources s ON s.id=c.source_id
            WHERE c.meeting_action_id=action_record.id AND c.active AND c.review_state<>'rejected'
              AND NOT s.deleted AND s.kind='meeting' AND s.id IS DISTINCT FROM sid
              AND EXISTS (SELECT 1 FROM meetings m WHERE m.id=s.record_id)
            ORDER BY c.occurred_on DESC NULLS LAST,c.id DESC LIMIT 1;
        IF alternative_meeting IS NOT NULL THEN
            UPDATE meeting_actions SET meeting_id=alternative_meeting,
                knowledge_version=knowledge_version+1 WHERE id=action_record.id;
            INSERT INTO knowledge_revisions(record_type,record_id,previous_version,origin,before_json,after_json,reason)
                SELECT 'meeting_action',id,action_record.knowledge_version,'system',to_jsonb(action_record),to_jsonb(a),
                       'Origem removida; ação preservada por outra fonte válida' FROM meeting_actions a WHERE id=action_record.id;
        ELSIF action_record.person_id IS NOT NULL AND
              (action_record.knowledge_human_fields<>'{}'::jsonb OR EXISTS (
                  SELECT 1 FROM knowledge_claims c LEFT JOIN knowledge_sources s ON s.id=c.source_id
                  WHERE c.meeting_action_id=action_record.id AND c.active AND c.review_state<>'rejected'
                    AND (c.source_id IS NULL OR NOT s.deleted))) THEN
            SELECT s.record_id INTO alternative_session FROM knowledge_claims c
                JOIN knowledge_sources s ON s.id=c.source_id
                WHERE c.meeting_action_id=action_record.id AND c.active AND c.review_state<>'rejected'
                  AND NOT s.deleted AND s.kind='one_on_one' AND s.id IS DISTINCT FROM sid
                  AND EXISTS (SELECT 1 FROM one_on_ones o WHERE o.id=s.record_id)
                ORDER BY c.occurred_on DESC NULLS LAST,c.id DESC LIMIT 1;
            INSERT INTO commitments (person_id,one_on_one_id,description,owner,due_date,status,closed_at,
                knowledge_version,knowledge_human_fields,knowledge_event_on,knowledge_created,assistant_request_id)
                VALUES (action_record.person_id,alternative_session,action_record.description,'person',action_record.due_date,
                    action_record.status,action_record.closed_at,action_record.knowledge_version+1,
                    action_record.knowledge_human_fields,action_record.knowledge_event_on,action_record.knowledge_created,
                    'knowledge:migrated_action:'||action_record.id)
                ON CONFLICT (assistant_request_id) DO UPDATE SET assistant_request_id=EXCLUDED.assistant_request_id
                RETURNING id INTO migrated_id;
            UPDATE knowledge_claims SET commitment_id=migrated_id,meeting_action_id=NULL,version=version+1,
                conflict=CASE WHEN conflict ? 'task_evidence_removed' THEN
                    jsonb_set(jsonb_set(conflict,'{task_evidence_removed,kind}','"commitment"'::jsonb),
                              '{task_evidence_removed,id}',to_jsonb(migrated_id)) ELSE conflict END
                WHERE meeting_action_id=action_record.id;
            UPDATE knowledge_revisions SET record_type='commitment',record_id=migrated_id
                WHERE record_type='meeting_action' AND record_id=action_record.id;
            SELECT to_jsonb(c) INTO migrated_record FROM commitments c WHERE id=migrated_id;
            INSERT INTO knowledge_revisions(record_type,record_id,previous_version,origin,before_json,after_json,reason)
                VALUES ('commitment',migrated_id,action_record.knowledge_version,'system',to_jsonb(action_record),migrated_record,
                        'Origem removida; tarefa e histórico preservados no cadastro de combinados');
            DELETE FROM meeting_actions WHERE id=action_record.id;
        END IF;
    END LOOP;
END $$;

-- cortex_knowledge_source_changed
CREATE OR REPLACE FUNCTION cortex_knowledge_source_changed() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE source_key_kind TEXT; sid BIGINT;
    BEGIN
        source_key_kind := CASE WHEN TG_TABLE_NAME='one_on_ones' THEN 'one_on_one' ELSE 'meeting' END;
        IF TG_OP='UPDATE' THEN
            IF TG_TABLE_NAME='one_on_ones' THEN
                IF OLD.transcript_raw IS NOT DISTINCT FROM NEW.transcript_raw
                   AND OLD.private_notes IS NOT DISTINCT FROM NEW.private_notes
                   AND OLD.public_notes IS NOT DISTINCT FROM NEW.public_notes
                   AND OLD.occurred_on IS NOT DISTINCT FROM NEW.occurred_on THEN RETURN NEW; END IF;
            ELSE
                IF OLD.transcript_raw IS NOT DISTINCT FROM NEW.transcript_raw
                   AND OLD.notes IS NOT DISTINCT FROM NEW.notes
                   AND OLD.occurred_on IS NOT DISTINCT FROM NEW.occurred_on THEN RETURN NEW; END IF;
            END IF;
        END IF;
        -- Cards antigos podem ter usado uma fonte ainda não enfileirada no backfill.
        IF TG_OP='DELETE' THEN DELETE FROM member_cards; END IF;
        SELECT id INTO sid FROM knowledge_sources WHERE kind=source_key_kind AND record_id=OLD.id FOR UPDATE;
        IF sid IS NULL THEN
            IF TG_OP='DELETE' THEN
                IF TG_TABLE_NAME='meetings' THEN PERFORM cortex_knowledge_preserve_actions(NULL,OLD.id); END IF;
                RETURN OLD;
            ELSE RETURN NEW; END IF;
        END IF;
        UPDATE knowledge_sources SET current_version_id=NULL,deleted=(TG_OP='DELETE'),
            updated_at=CURRENT_TIMESTAMP WHERE id=sid;
        UPDATE knowledge_claims SET active=FALSE WHERE source_id=sid;
        UPDATE knowledge_relations SET active=FALSE WHERE claim_id IN
            (SELECT id FROM knowledge_claims WHERE source_id=sid);
        UPDATE knowledge_jobs SET status='superseded',lease_token=NULL,lease_until=NULL,
            staged_json='[]'::jsonb,updated_at=CURRENT_TIMESTAMP WHERE source_id=sid;
        INSERT INTO knowledge_outbox(kind,person_id,revision_token)
            SELECT 'cards',id,'source:'||sid||':'||CURRENT_TIMESTAMP::text FROM people;
        IF TG_OP='DELETE' THEN
            IF TG_TABLE_NAME='meetings' THEN PERFORM cortex_knowledge_preserve_actions(sid,OLD.id); END IF;
            PERFORM cortex_knowledge_flag_removed_support(sid);
            UPDATE commitments t SET status='cancelled',closed_at=CURRENT_TIMESTAMP,
                description='Registro removido com a fonte',due_date=NULL,
                knowledge_version=knowledge_version+1,updated_at=CURRENT_TIMESTAMP
                WHERE t.knowledge_created AND t.knowledge_human_fields='{}'::jsonb
                AND t.id IN (SELECT commitment_id FROM knowledge_claims WHERE source_id=sid)
                AND NOT EXISTS (SELECT 1 FROM knowledge_claims c WHERE c.commitment_id=t.id
                    AND c.active AND c.review_state<>'rejected');
            UPDATE meeting_actions t SET status='cancelled',closed_at=CURRENT_TIMESTAMP,
                description='Registro removido com a fonte',due_date=NULL,
                knowledge_version=knowledge_version+1,updated_at=CURRENT_TIMESTAMP
                WHERE t.knowledge_created AND t.knowledge_human_fields='{}'::jsonb
                AND t.id IN (SELECT meeting_action_id FROM knowledge_claims WHERE source_id=sid)
                AND NOT EXISTS (SELECT 1 FROM knowledge_claims c WHERE c.meeting_action_id=t.id
                    AND c.active AND c.review_state<>'rejected');
            DELETE FROM knowledge_revisions WHERE origin IN ('automatic','system') AND (
                (record_type='commitment' AND record_id IN (SELECT id FROM commitments WHERE knowledge_created
                    AND knowledge_human_fields='{}'::jsonb AND description='Registro removido com a fonte'))
                OR (record_type='meeting_action' AND record_id IN (SELECT id FROM meeting_actions WHERE knowledge_created
                    AND knowledge_human_fields='{}'::jsonb AND description='Registro removido com a fonte')));
            DELETE FROM knowledge_revisions WHERE
                (record_type IN ('claim','knowledge_claim') AND record_id IN (SELECT id FROM knowledge_claims WHERE source_id=sid))
                OR (record_type IN ('relation','knowledge_relation') AND record_id IN (SELECT r.id FROM knowledge_relations r
                    JOIN knowledge_claims c ON c.id=r.claim_id WHERE c.source_id=sid));
            DELETE FROM knowledge_versions WHERE source_id=sid;
            DELETE FROM knowledge_entities e WHERE e.kind IN ('project','topic')
                AND NOT EXISTS (SELECT 1 FROM knowledge_claims c WHERE c.speaker_entity_id=e.id OR c.subject_entity_id=e.id
                    OR c.owner_entity_id=e.id OR c.entity_ids @> jsonb_build_array(e.id))
                AND NOT EXISTS (SELECT 1 FROM knowledge_relations r WHERE r.from_entity_id=e.id OR r.to_entity_id=e.id)
                AND NOT EXISTS (SELECT 1 FROM knowledge_revisions r WHERE r.record_type='entity' AND r.record_id=e.id AND r.origin='human');
            UPDATE knowledge_sources SET title='Fonte removida' WHERE id=sid;
            RETURN OLD;
        END IF;
        RETURN NEW;
    END $$;

-- one_on_ones.drop_knowledge_trigger
DROP TRIGGER IF EXISTS cortex_knowledge_source_changed ON one_on_ones;

-- one_on_ones.drop_knowledge_update_trigger
DROP TRIGGER IF EXISTS cortex_knowledge_source_update ON one_on_ones;

-- one_on_ones.drop_knowledge_delete_trigger
DROP TRIGGER IF EXISTS cortex_knowledge_source_delete ON one_on_ones;

-- one_on_ones.knowledge_update_trigger
CREATE TRIGGER cortex_knowledge_source_update AFTER UPDATE OF transcript_raw,private_notes,public_notes,occurred_on
            ON one_on_ones FOR EACH ROW EXECUTE FUNCTION cortex_knowledge_source_changed();

-- one_on_ones.knowledge_delete_trigger
CREATE TRIGGER cortex_knowledge_source_delete BEFORE DELETE ON one_on_ones
            FOR EACH ROW EXECUTE FUNCTION cortex_knowledge_source_changed();

-- meetings.drop_knowledge_trigger
DROP TRIGGER IF EXISTS cortex_knowledge_source_changed ON meetings;

-- meetings.drop_knowledge_update_trigger
DROP TRIGGER IF EXISTS cortex_knowledge_source_update ON meetings;

-- meetings.drop_knowledge_delete_trigger
DROP TRIGGER IF EXISTS cortex_knowledge_source_delete ON meetings;

-- meetings.knowledge_update_trigger
CREATE TRIGGER cortex_knowledge_source_update AFTER UPDATE OF transcript_raw,notes,occurred_on
            ON meetings FOR EACH ROW EXECUTE FUNCTION cortex_knowledge_source_changed();

-- meetings.knowledge_delete_trigger
CREATE TRIGGER cortex_knowledge_source_delete BEFORE DELETE ON meetings
            FOR EACH ROW EXECUTE FUNCTION cortex_knowledge_source_changed();

-- cortex_knowledge_flag_removed_support.revoke
REVOKE EXECUTE ON FUNCTION cortex_knowledge_flag_removed_support(BIGINT) FROM PUBLIC;

-- cortex_knowledge_source_changed.revoke
REVOKE EXECUTE ON FUNCTION cortex_knowledge_source_changed() FROM PUBLIC;

-- cortex_knowledge_preserve_actions.revoke
REVOKE EXECUTE ON FUNCTION cortex_knowledge_preserve_actions(BIGINT,INTEGER) FROM PUBLIC;

-- knowledge_helpers.revoke_api_roles
DO $$
DECLARE api_role TEXT; helper_signature TEXT;
BEGIN
    FOR api_role IN SELECT rolname FROM pg_catalog.pg_roles
        WHERE rolname IN ('anon','authenticated','service_role')
    LOOP
        FOREACH helper_signature IN ARRAY ARRAY[
            'public.cortex_knowledge_flag_removed_support(BIGINT)',
            'public.cortex_knowledge_source_changed()',
            'public.cortex_knowledge_preserve_actions(BIGINT,INTEGER)'
        ]
        LOOP
            EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM %I',helper_signature,api_role);
        END LOOP;
    END LOOP;
END $$;

COMMIT;

-- 4. Verificação somente-leitura: devem aparecer as oito tabelas com rls_ativa=true.
SELECT tablename AS tabela, rowsecurity AS rls_ativa, tableowner AS proprietario
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'knowledge_entities', 'knowledge_sources', 'knowledge_versions',
    'knowledge_claims', 'knowledge_relations', 'knowledge_revisions',
    'knowledge_jobs', 'knowledge_outbox'
  )
ORDER BY tablename;
