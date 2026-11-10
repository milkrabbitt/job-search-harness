"""Private SQLite state and atomic transactions."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

def now():
    return datetime.now(TZ).replace(microsecond=0)

def stamp():
    return now().isoformat()

def shift(seconds):
    return (now() + timedelta(seconds=seconds)).isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT NOT NULL,
 source TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(key,source));
CREATE TABLE IF NOT EXISTS resumes(id INTEGER PRIMARY KEY, label TEXT NOT NULL, path TEXT NOT NULL,
 sha256 TEXT NOT NULL UNIQUE, modified_at TEXT, scope TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS exclusions(pattern TEXT PRIMARY KEY, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channels(name TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, next_check TEXT);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, channel TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
 company TEXT, domain TEXT, location TEXT, recipient TEXT, recipient_source TEXT, enabled INTEGER NOT NULL DEFAULT 1,
 last_checked TEXT, last_status TEXT);
CREATE TABLE IF NOT EXISTS employers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
 domain TEXT, identity TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS employer_followups(employer_id INTEGER PRIMARY KEY REFERENCES employers,
 evidence TEXT NOT NULL, updated_at TEXT NOT NULL, released_at TEXT);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, employer_id INTEGER REFERENCES employers, company TEXT NOT NULL,
 title TEXT NOT NULL, title_key TEXT NOT NULL, location TEXT, salary TEXT, description TEXT, requirements TEXT,
 channel TEXT NOT NULL, url TEXT, domain TEXT, status TEXT NOT NULL, published_at TEXT, observed_at TEXT NOT NULL,
 created_at TEXT NOT NULL, match_json TEXT, match_score INTEGER);
CREATE TABLE IF NOT EXISTS job_aliases(channel TEXT NOT NULL, external_id TEXT NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs,
 url TEXT, PRIMARY KEY(channel,external_id));
CREATE TABLE IF NOT EXISTS job_reviews(job_id INTEGER PRIMARY KEY REFERENCES jobs, state TEXT NOT NULL,
 source TEXT NOT NULL, note TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, employer_id INTEGER REFERENCES employers, channel TEXT NOT NULL,
 address TEXT NOT NULL, source TEXT NOT NULL, verified_at TEXT NOT NULL, UNIQUE(channel,address,employer_id));
CREATE TABLE IF NOT EXISTS job_contacts(job_id INTEGER PRIMARY KEY REFERENCES jobs, contact_id INTEGER NOT NULL REFERENCES contacts);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs, channel TEXT NOT NULL, company TEXT NOT NULL,
 title TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL, evidence TEXT, occurred_at TEXT,
 recipient TEXT, fingerprint TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS threads(id INTEGER PRIMARY KEY, channel TEXT NOT NULL, external_id TEXT NOT NULL, company TEXT,
 subject TEXT, job_id INTEGER REFERENCES jobs, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 stopped INTEGER NOT NULL DEFAULT 0, UNIQUE(channel,external_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, channel TEXT NOT NULL, thread_id INTEGER NOT NULL REFERENCES threads,
 external_id TEXT NOT NULL, direction TEXT NOT NULL, sender TEXT, subject TEXT, body TEXT, occurred_at TEXT,
 message_id TEXT, refs TEXT, classification TEXT, UNIQUE(channel,external_id), UNIQUE(channel,message_id));
CREATE TABLE IF NOT EXISTS mail_metadata(message_id INTEGER PRIMARY KEY REFERENCES messages ON DELETE CASCADE,
 recipient TEXT, reply_to TEXT, in_reply_to TEXT, mail_date TEXT, auto_submitted TEXT, delivery_status TEXT,
 reply_to_unverified INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0,
 sender_authenticated INTEGER NOT NULL DEFAULT 0,
 baseline INTEGER NOT NULL DEFAULT 1, observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail_audits(message_id INTEGER PRIMARY KEY REFERENCES messages ON DELETE CASCADE,
 job_id INTEGER REFERENCES jobs, history_id INTEGER REFERENCES history, state TEXT NOT NULL,
 evidence TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail_baseline_evidence(external_id TEXT PRIMARY KEY, job_id INTEGER REFERENCES jobs,
 history_id INTEGER REFERENCES history, state TEXT, company TEXT, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS platform_message_sources(message_id INTEGER PRIMARY KEY REFERENCES messages ON DELETE CASCADE,
 source_url TEXT NOT NULL, platform_job_id TEXT NOT NULL, imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS browser_actions(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs,
 channel TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
 anchor_id INTEGER REFERENCES messages, thread_revision INTEGER, resume_id INTEGER REFERENCES resumes, attachment_sha TEXT,
 state TEXT NOT NULL, evidence TEXT, receipt TEXT, claim_token TEXT, claimed_at TEXT, claim_until TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_browser_send ON browser_actions(state) WHERE state='sending';
CREATE INDEX IF NOT EXISTS idx_browser_job ON browser_actions(job_id,kind,state);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs, thread_id INTEGER REFERENCES threads,
 anchor_id INTEGER REFERENCES messages, thread_revision INTEGER, channel TEXT NOT NULL, kind TEXT NOT NULL,
 to_address TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, resume_id INTEGER REFERENCES resumes,
 attachment_sha TEXT, message_id TEXT NOT NULL UNIQUE, in_reply_to TEXT, refs TEXT, state TEXT NOT NULL,
 approved INTEGER NOT NULL DEFAULT 0, policy_generated INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 evidence TEXT, provider_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, due_at TEXT,
 approval_hash TEXT, stop_generation INTEGER, retryable INTEGER NOT NULL DEFAULT 0, UNIQUE(thread_id,anchor_id,kind));
CREATE UNIQUE INDEX IF NOT EXISTS single_job_application ON outbox(job_id) WHERE kind='application';
CREATE UNIQUE INDEX IF NOT EXISTS single_thread_followup ON outbox(thread_id) WHERE kind='followup';
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, thread_id INTEGER REFERENCES threads,
 message_id INTEGER REFERENCES messages, kind TEXT NOT NULL, question TEXT NOT NULL, draft TEXT, state TEXT NOT NULL,
 due_at TEXT, resolution TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(message_id,kind));
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, channel TEXT NOT NULL,
 dedupe_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 due_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT, cancelled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task_steps(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks,
 stage TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL, job_id INTEGER REFERENCES jobs,
 outbox_id INTEGER REFERENCES outbox, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, level TEXT NOT NULL,
 channel TEXT, message TEXT NOT NULL, event_key TEXT UNIQUE, notified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS model_runs(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL, state TEXT NOT NULL,
 detail TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(state,due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(state,due_at);
CREATE INDEX IF NOT EXISTS idx_history_job ON history(job_id,status);
CREATE INDEX IF NOT EXISTS idx_jobs_title ON jobs(title_key);
CREATE INDEX IF NOT EXISTS idx_msg_thread ON messages(thread_id,id);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id,id);
"""

class Store:
    def __init__(self, data_dir):
        self.root = Path(data_dir).expanduser().resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path = self.root / "harness.sqlite3"
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(outbox)")}
            if "origin_task_id" not in columns:
                connection.execute("ALTER TABLE outbox ADD COLUMN origin_task_id INTEGER REFERENCES tasks")
            if "attachment_filename" not in columns:
                connection.execute("ALTER TABLE outbox ADD COLUMN attachment_filename TEXT")
            if "sender_authenticated" not in {row[1] for row in connection.execute("PRAGMA table_info(mail_metadata)")}:
                connection.execute("ALTER TABLE mail_metadata ADD COLUMN sender_authenticated INTEGER NOT NULL DEFAULT 0")
        os.chmod(self.path, 0o600)

    def connect(self):
        connection = sqlite3.connect(str(self.path), isolation_level=None, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        return connection

    @contextmanager
    def tx(self, write=False):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self):
        folder = self.root / "backups"
        folder.mkdir(exist_ok=True, mode=0o700)
        target = folder / ("harness-" + now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3")
        with self.connect() as source, sqlite3.connect(str(target)) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("数据库备份完整性校验失败")
        os.chmod(target, 0o600)
        return target

def fetch(connection, sql, params=()):
    return [dict(row) for row in connection.execute(sql, params)]

def one(connection, sql, params=()):
    row = connection.execute(sql, params).fetchone()
    return dict(row) if row else None


# An unverified same-name listing waits for human identity review. Distinct verified domains remain separate.
FOLLOWUP_EMPLOYER_MATCH = """(candidate.id=held.id OR (candidate.name_key=held.name_key
    AND (NULLIF(candidate.domain,'') IS NULL OR NULLIF(held.domain,'') IS NULL OR candidate.domain=held.domain)))"""


def manual_followup(connection, job_id):
    if not job_id:
        return None
    return one(connection, f"""SELECT f.*,held.name FROM employer_followups f
        JOIN employers held ON held.id=f.employer_id JOIN jobs j ON j.id=?
        JOIN employers candidate ON candidate.id=j.employer_id
        WHERE f.released_at IS NULL AND {FOLLOWUP_EMPLOYER_MATCH}
        ORDER BY (candidate.id=held.id) DESC,f.employer_id LIMIT 1""", (job_id,))
