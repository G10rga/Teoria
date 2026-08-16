-- Prava: Georgian driving theory exam trainer
-- SQLite schema

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tickets (
    id            INTEGER PRIMARY KEY,          -- ticket number on teoria.on.ge
    category      TEXT    NOT NULL DEFAULT 'B',
    question      TEXT    NOT NULL,
    answers_json  TEXT    NOT NULL,             -- JSON array of answer strings
    correct_index INTEGER,                      -- 0-based; NULL = unknown (needs scraping fix)
    image         TEXT,                         -- relative path under static/, or NULL
    layout        TEXT,                         -- site layout classes: cutoff-N answers-num-N ...
    explanation   TEXT,
    source_url    TEXT,
    imported_at   TEXT
);

-- One row per generated exam. A "cycle" is a full shuffle of the whole bank:
-- 921 tickets -> 30 exams of 30 + 1 exam of 21 = 31 exams, zero repeats.
CREATE TABLE IF NOT EXISTS exams (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle      INTEGER NOT NULL DEFAULT 1,
    number     INTEGER,                         -- 1..31 inside the cycle (NULL for review exams)
    kind       TEXT    NOT NULL DEFAULT 'base', -- 'base' | 'review'
    seed       TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_tickets (
    exam_id   INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,                 -- 1..30
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    PRIMARY KEY (exam_id, position)
);

CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id       INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    mode          TEXT    NOT NULL DEFAULT 'exam',   -- 'exam' | 'practice'
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    correct_count INTEGER,
    wrong_count   INTEGER,
    passed        INTEGER,                           -- 0/1
    seconds_spent INTEGER
);

CREATE TABLE IF NOT EXISTS attempt_answers (
    attempt_id   INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id),
    chosen_index INTEGER,                            -- NULL = left blank
    is_correct   INTEGER,
    answered_at  TEXT,
    PRIMARY KEY (attempt_id, ticket_id)
);

-- Per-ticket learning state, the thing that drives review exams.
CREATE TABLE IF NOT EXISTS ticket_stats (
    ticket_id      INTEGER PRIMARY KEY REFERENCES tickets(id),
    seen           INTEGER NOT NULL DEFAULT 0,
    wrong          INTEGER NOT NULL DEFAULT 0,
    right_count    INTEGER NOT NULL DEFAULT 0,
    correct_streak INTEGER NOT NULL DEFAULT 0,
    mastered       INTEGER NOT NULL DEFAULT 0,
    last_seen      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_exam_tickets_ticket ON exam_tickets(ticket_id);
CREATE INDEX IF NOT EXISTS idx_attempts_exam ON attempts(exam_id);
CREATE INDEX IF NOT EXISTS idx_stats_wrong ON ticket_stats(wrong DESC, mastered);
