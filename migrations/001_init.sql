-- Схема базы. Версия хранится в PRAGMA user_version, накат идемпотентный.
-- Все имена латиницей: кириллица в идентификаторах ломается молча.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Источник как конфигурация, а не хардкод в коде.
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    homepage_url    TEXT NOT NULL,
    feed_url        TEXT NOT NULL,
    fulltext_field  TEXT,
    selector        TEXT,
    prefer          TEXT,                    -- feed | selector | trafilatura
    extra_headers   TEXT,                    -- JSON
    why             TEXT,                    -- зачем этот источник в подборке
    etag            TEXT,
    last_modified   TEXT,
    robots_checked_at TEXT,
    robots_allows   INTEGER,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_fetch_at   TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id                  INTEGER PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES sources(id),
    guid                TEXT,
    url                 TEXT NOT NULL,
    url_canon           TEXT NOT NULL,
    title               TEXT NOT NULL,
    body_text           TEXT,
    -- Единое окно исходного текста: ровно то, что увидят и модель, и человек слева на экране сравнения.
    rewrite_source_text TEXT,
    source_text_sha256  TEXT,
    source_len          INTEGER,
    author              TEXT,
    image_url           TEXT,
    published_at        TEXT,
    fetched_at          TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    word_count          INTEGER,
    fact_density        INTEGER,
    extraction_method   TEXT,                -- feed | selector | trafilatura | jsonld
    extraction_quality  REAL,
    sensitivity         TEXT DEFAULT 'unknown',
    sensitivity_reason  TEXT,
    in_showcase         INTEGER NOT NULL DEFAULT 0,
    moods_ready         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'new',   -- new | ready | failed
    url_checked_at      TEXT,
    url_http_status     INTEGER,
    raw_entry_json      TEXT,
    snapshot_path       TEXT,
    snapshot_sha256     TEXT,
    error_note          TEXT,
    UNIQUE (url_canon),
    UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS idx_articles_showcase ON articles(in_showcase, status, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source   ON articles(source_id, published_at DESC);

-- Факты со смещениями: смещения — основа парной подсветки на экране сравнения.
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY,
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,               -- number|percent|money|date|person|org|geo|quote
    surface     TEXT NOT NULL,
    norm        TEXT,
    value_num   REAL,
    unit        TEXT,
    approx      TEXT,
    char_start  INTEGER,
    char_end    INTEGER,
    in_title    INTEGER NOT NULL DEFAULT 0,
    weight      REAL NOT NULL DEFAULT 1.0,
    extractor   TEXT,                        -- rules | ner | dateparser | llm
    confidence  REAL
);
CREATE INDEX IF NOT EXISTS idx_facts_article ON facts(article_id);

-- Тона живут в базе, а не в коде: промпт можно показать в интерфейсе кнопкой.
CREATE TABLE IF NOT EXISTS moods (
    code                  TEXT PRIMARY KEY,
    title_ru              TEXT NOT NULL,
    subtitle              TEXT,
    accent_hue            INTEGER,
    system_prompt         TEXT NOT NULL,
    temperature           REAL NOT NULL DEFAULT 0.8,
    max_tokens            INTEGER NOT NULL DEFAULT 1500,
    prompt_version        TEXT NOT NULL DEFAULT 'v1',
    allowed_max_sensitivity TEXT NOT NULL DEFAULT 'none',
    is_service            INTEGER NOT NULL DEFAULT 0,
    is_active             INTEGER NOT NULL DEFAULT 1,
    sort_order            INTEGER NOT NULL DEFAULT 0
);

-- Каждый вызов модели: одновременно кэш, кассета для воспроизведения и доказательство,
-- что текст породила модель, а не рука.
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY,
    request_hash  TEXT NOT NULL UNIQUE,
    purpose       TEXT NOT NULL,             -- rewrite | repair | extract | sensitivity | judge
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    request_json  TEXT,
    prompt_sha256 TEXT,
    response_text TEXT,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    cost_rub      REAL DEFAULT 0,
    latency_ms    INTEGER,
    http_status   INTEGER,
    error         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rewrites (
    id                 INTEGER PRIMARY KEY,
    article_id         INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    mood_code          TEXT NOT NULL REFERENCES moods(code),
    llm_call_id        INTEGER REFERENCES llm_calls(id),
    generator          TEXT NOT NULL DEFAULT 'llm',   -- llm | code | hybrid
    title_rewritten    TEXT,
    body_rewritten     TEXT,
    provider           TEXT,
    model              TEXT,
    prompt_version     TEXT,
    prompt_sha256      TEXT,
    source_text_sha256 TEXT,
    temperature        REAL,
    attempt            INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'draft',  -- draft|published|failed|degraded
    verdict            TEXT,                           -- PASS|COPY|FAIL|TOO_SHORT|TOO_LONG|WRONG_LANGUAGE
    check_score        REAL,
    style_distance     REAL,
    length_ratio       REAL,
    cyrillic_ratio     REAL,
    facts_total        INTEGER,
    facts_kept         INTEGER,
    facts_missing      INTEGER,
    facts_altered      INTEGER,
    hallucinated_count INTEGER,
    tokens_in          INTEGER,
    tokens_out         INTEGER,
    cost_rub           REAL DEFAULT 0,
    latency_ms         INTEGER,
    created_at         TEXT NOT NULL,
    -- Текст без записи вызова модели допустим только у детерминированного «Скелета».
    CHECK (llm_call_id IS NOT NULL OR generator = 'code')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rewrites_published
    ON rewrites(article_id, mood_code) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_rewrites_article ON rewrites(article_id, mood_code);

CREATE TABLE IF NOT EXISTS fact_checks (
    id            INTEGER PRIMARY KEY,
    rewrite_id    INTEGER NOT NULL REFERENCES rewrites(id) ON DELETE CASCADE,
    fact_id       INTEGER REFERENCES facts(id) ON DELETE CASCADE,
    scope         TEXT NOT NULL DEFAULT 'body',   -- body | title
    verdict       TEXT NOT NULL,
    severity      INTEGER NOT NULL DEFAULT 0,
    found_surface TEXT,
    found_start   INTEGER,
    found_end     INTEGER,
    match_method  TEXT,
    score         REAL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_rewrite ON fact_checks(rewrite_id);

CREATE TABLE IF NOT EXISTS hallucinations (
    id         INTEGER PRIMARY KEY,
    rewrite_id INTEGER NOT NULL REFERENCES rewrites(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    surface    TEXT NOT NULL,
    norm       TEXT,
    char_start INTEGER,
    char_end   INTEGER
);

CREATE TABLE IF NOT EXISTS semantic_checks (
    id          INTEGER PRIMARY KEY,
    rewrite_id  INTEGER NOT NULL REFERENCES rewrites(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    detail_json TEXT,
    severity    INTEGER NOT NULL DEFAULT 0
);

-- Наблюдаемость: без неё раздел README «что ломалось» пришлось бы писать по памяти.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    items_in    INTEGER DEFAULT 0,
    items_out   INTEGER DEFAULT 0,
    errors      INTEGER DEFAULT 0,
    cost_rub    REAL DEFAULT 0,
    cache_hits  INTEGER DEFAULT 0,
    log_excerpt TEXT
);

CREATE TABLE IF NOT EXISTS source_health (
    id                     INTEGER PRIMARY KEY,
    source_code            TEXT NOT NULL,
    checked_at             TEXT NOT NULL,
    ok                     INTEGER NOT NULL,
    http_status            INTEGER,
    latency_ms             INTEGER,
    items                  INTEGER,
    full_text_ratio        REAL,
    quality_gate_pass_ratio REAL,
    note                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_health_source ON source_health(source_code, checked_at DESC);

CREATE TABLE IF NOT EXISTS provider_health (
    id         INTEGER PRIMARY KEY,
    provider   TEXT NOT NULL,
    model      TEXT,
    checked_at TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    http_status INTEGER,
    latency_ms INTEGER,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    id                 INTEGER PRIMARY KEY,
    run_id             TEXT,
    kind               TEXT NOT NULL,
    stage              TEXT,
    source_or_provider TEXT,
    http_status        INTEGER,
    message            TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_kind ON incidents(kind, created_at DESC);

PRAGMA user_version = 1;
