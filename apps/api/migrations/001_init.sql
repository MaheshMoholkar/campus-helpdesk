-- Chat API tables (docs/spec.md section 11). Runs with search_path = helpdesk, public.

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- 11.7 reference data ---------------------------------------------------------

CREATE TABLE colleges (
    code text PRIMARY KEY,
    name text NOT NULL
);

INSERT INTO colleges (code, name) VALUES ('all', 'All colleges');

CREATE TABLE offices (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL,
    college_code text NOT NULL REFERENCES colleges (code),
    category     text NOT NULL,
    email        text,
    phone        text,
    hours        text,
    UNIQUE (college_code, category)
);

-- 11.2 documents --------------------------------------------------------------

CREATE TABLE documents (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL UNIQUE,
    title        text NOT NULL,
    doc_type     text NOT NULL CHECK (doc_type IN ('circular', 'policy', 'placement_notice', 'faq')),
    college_code text NOT NULL REFERENCES colleges (code),
    audience     text NOT NULL CHECK (audience IN ('public', 'student', 'staff')),
    category     text NOT NULL CHECK (category IN
                     ('exam', 'fee', 'hostel', 'admission', 'placement', 'general')),
    language     text NOT NULL CHECK (language IN ('en', 'hi', 'mr')),
    issue_date   date NOT NULL,
    expires_on   date,
    series_key   text,
    version      int  NOT NULL DEFAULT 1,
    is_current   boolean NOT NULL DEFAULT true,
    reference_no text,
    content_hash text NOT NULL,
    body         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (series_key, version)
);

-- The database itself refuses two live versions of one series.
CREATE UNIQUE INDEX documents_one_current_per_series
    ON documents (series_key)
    WHERE is_current AND series_key IS NOT NULL;

-- 11.3 chunks -----------------------------------------------------------------

CREATE TABLE chunks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index  int  NOT NULL,
    heading      text,
    text         text NOT NULL,
    embedding    vector(1024) NOT NULL,
    tsv          tsvector NOT NULL,
    -- copied from the parent document at ingest; these are what the scope filter reads
    college_code text NOT NULL,
    audience     text NOT NULL,
    category     text NOT NULL,
    language     text NOT NULL,
    issue_date   date NOT NULL,
    expires_on   date,
    is_current   boolean NOT NULL,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_scope_idx ON chunks (audience, college_code);
-- No vector index on purpose: exact search is fast and always correct at this size.

-- 11.4 conversation -----------------------------------------------------------

CREATE TABLE conversations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub     text,
    role         text NOT NULL CHECK (role IN ('anonymous', 'student', 'staff')),
    college_code text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE turns (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id    uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    seq                int  NOT NULL,
    speaker            text NOT NULL CHECK (speaker IN ('user', 'assistant')),
    text               text NOT NULL,
    language           text,
    rewritten_question text,
    intent             text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);

-- 11.5 operational tables -----------------------------------------------------

CREATE TABLE answers (
    turn_id           uuid PRIMARY KEY REFERENCES turns (id) ON DELETE CASCADE,
    outcome           text NOT NULL CHECK (outcome IN ('answered', 'abstained', 'refused', 'tool')),
    model             text NOT NULL,
    prompt_version    text NOT NULL,
    prompt_tokens     int,
    completion_tokens int,
    retrieval_ms      int,
    rerank_ms         int,
    first_token_ms    int,
    total_ms          int,
    top_score         real,
    trace_id          text
);

CREATE TABLE citations (
    turn_id     uuid NOT NULL REFERENCES answers (turn_id) ON DELETE CASCADE,
    position    int  NOT NULL,
    document_id uuid NOT NULL,  -- no FK: kept even if the document is replaced
    chunk_id    uuid REFERENCES chunks (id) ON DELETE SET NULL,
    score       real,
    PRIMARY KEY (turn_id, position)
);

CREATE TABLE feedback (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id    uuid NOT NULL REFERENCES turns (id) ON DELETE CASCADE,
    rating     smallint NOT NULL CHECK (rating IN (-1, 1)),
    comment    text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE unanswered (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id      uuid NOT NULL REFERENCES turns (id) ON DELETE CASCADE,
    question     text NOT NULL,
    language     text,
    role         text NOT NULL,
    college_code text,
    top_score    real,
    office_id    uuid REFERENCES offices (id),
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pending_actions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    user_sub        text NOT NULL,
    tool_name       text NOT NULL,
    arguments       jsonb NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'executed', 'cancelled', 'expired')),
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    executed_at     timestamptz
);
