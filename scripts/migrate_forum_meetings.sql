-- Cortex: reuniões de fóruns, participantes e ações.
-- Executar na base existente do Cortex (PostgreSQL / SQL Editor do Supabase).
-- Idempotente: preserva os registros existentes.

BEGIN;

CREATE TABLE IF NOT EXISTS meetings (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    forum VARCHAR(255),
    occurred_on DATE NOT NULL,
    external_participants TEXT,
    transcript_raw TEXT,
    notes TEXT,
    summary_ai TEXT,
    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    extraction_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings (occurred_on DESC, id DESC);

CREATE TABLE IF NOT EXISTS meeting_participants (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (meeting_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_meeting_participants_person ON meeting_participants (person_id, meeting_id);

CREATE TABLE IF NOT EXISTS meeting_actions (
    id SERIAL PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    owner_name TEXT,
    due_date DATE,
    status VARCHAR(15) NOT NULL DEFAULT 'open'
        CONSTRAINT chk_meeting_actions_status CHECK (status IN ('open', 'done', 'cancelled')),
    source_key TEXT,
    closed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (meeting_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_meeting_actions_person ON meeting_actions (person_id, status);

CREATE INDEX IF NOT EXISTS idx_meeting_actions_meeting ON meeting_actions (meeting_id, status);

COMMIT;
