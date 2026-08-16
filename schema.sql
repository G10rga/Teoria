-- Prava: Georgian driving theory exam trainer
-- Schema is created by SQLAlchemy (models.py). This file documents the tables.

-- Shared question bank (all users)
CREATE TABLE IF NOT EXISTS tickets (
    id            INTEGER PRIMARY KEY,
    category      TEXT    NOT NULL DEFAULT 'B',
    question      TEXT    NOT NULL,
    answers_json  TEXT    NOT NULL,
    correct_index INTEGER,
    image         TEXT,
    layout        TEXT,
    explanation   TEXT,
    source_url    TEXT,
    imported_at   DATETIME
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    DATETIME NOT NULL,
    last_login_at DATETIME
);

-- One generated exam per user. A cycle partitions the bank with no repeats.
CREATE TABLE IF NOT EXISTS exams (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle      INTEGER NOT NULL DEFAULT 1,
    number     INTEGER,
    kind       TEXT    NOT NULL DEFAULT 'base',
    seed       TEXT,
    created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_tickets (
    exam_id   INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    PRIMARY KEY (exam_id, position)
);

CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exam_id       INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    mode          TEXT    NOT NULL DEFAULT 'exam',
    started_at    DATETIME NOT NULL,
    finished_at   DATETIME,
    correct_count INTEGER,
    wrong_count   INTEGER,
    passed        BOOLEAN,
    seconds_spent INTEGER
);

CREATE TABLE IF NOT EXISTS attempt_answers (
    attempt_id   INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id),
    chosen_index INTEGER,
    is_correct   BOOLEAN,
    answered_at  DATETIME,
    PRIMARY KEY (attempt_id, ticket_id)
);

-- Per-user learning state; review exams are built from wrong > 0 AND mastered = 0.
CREATE TABLE IF NOT EXISTS ticket_stats (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticket_id      INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    seen           INTEGER NOT NULL DEFAULT 0,
    wrong          INTEGER NOT NULL DEFAULT 0,
    right_count    INTEGER NOT NULL DEFAULT 0,
    correct_streak INTEGER NOT NULL DEFAULT 0,
    mastered       BOOLEAN NOT NULL DEFAULT 0,
    last_seen      DATETIME,
    PRIMARY KEY (user_id, ticket_id)
);

-- Individual wrong/blank answers, resolved when the ticket is mastered.
CREATE TABLE IF NOT EXISTS failed_questions (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    attempt_id   INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    chosen_index INTEGER,
    created_at   DATETIME NOT NULL,
    resolved_at  DATETIME
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
