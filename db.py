import sqlite3
from contextlib import contextmanager
from difflib import SequenceMatcher

DB_PATH = "qa_pipeline.db"

_USE_CASE_PROMPT = """\
You are an expert Principal Product Manager and Systems Architect.

Your task is to analyze the provided source data and produce a COMPLETE and EXHAUSTIVE set \
of technical Use Cases. Every functional requirement must result in at least one use case.

STEP 1 — LIST ALL REQUIREMENTS:
Begin your response by listing every distinct functional requirement, user-facing feature, \
or system capability you identify in the source document. Number each one. Do not group, \
combine, or omit any items. Format this section exactly as:

REQUIREMENTS IDENTIFIED:
1. [Requirement name]
2. [Requirement name]
...

STEP 2 — GENERATE USE CASES:
For every numbered item in your list above, write a complete use case block. \
Every requirement must have a corresponding use case — do not skip any.

For each use case write out three distinct structural paths:
1. Main Success Scenario (Happy Path)
2. Alternative Scenario (Negative/Validation Failure Path)
3. Exception Scenario (Edge Case/System/Network Timeout Path)

You must also assign a business PRIORITY to every use case, classified as exactly one of: \
Critical, High, Normal, or Low. Base this on how central the capability is to the system's \
core purpose and the impact of it failing — Critical for core/security-critical paths whose \
failure blocks primary functionality, High for important but non-blocking capabilities, \
Normal for standard supporting functionality, and Low for minor/cosmetic or rarely-used paths.

CRITICAL FORMATTING RULES:
You must format every single use case block using this EXACT header line pattern so our \
regex parser can split them:
### USE CASE: [Short Descriptive Feature Title]

Follow that header exactly with these fields:
- **Description:** [Brief summary of interaction objective]
- **Priority:** [Critical, High, Normal, or Low]
- **Actors:** [User, API Client, Database, Background Worker, etc]
- **Pre-conditions:** [System state required before execution begins]
- **Main Success Scenario Steps:**
  1. [Step 1 description]
  2. [Step 2 description]
- **Alternative Scenario Steps (Negative Path):**
  1. [What happens when validation fails]
- **Exception Scenario Steps (Edge Case):**
  1. [What happens during system crashes or limits]

Output the REQUIREMENTS IDENTIFIED list first, then the use case blocks. \
Do not add any other conversational text.\
"""

_TEST_CASE_PROMPT = """\
You are an expert QA Automation Engineer. Your task is to analyze the provided source document \
and generate a comprehensive suite of QA test cases.

For each capability or path analyzed, you must map out:
1. Happy Path cases
2. Negative/Boundary cases
3. Edge cases

CRITICAL INSTRUCTION: You must output ONLY a raw JSON array of objects. Do not wrap the \
JSON in markdown code blocks like ```json ... ```. Do not include any conversational text.

Each JSON object in the array must strictly use this schema:
{
  "title": "[Component/Feature] - Short descriptive title",
  "use_case": "[Exact USE CASE identifier this test belongs to, e.g. SYS-1 (shown in the '=== USE CASE: [ID] ... ===' context header), or empty string]",
  "description": "**Pre-conditions:**\\n- State of system\\n\\n**Test Steps:**\\n1. Step one\\n2. Step two\\n\\n**Expected Result:**\\n- Expected outcome"
}\
"""

_DEFAULT_SETTINGS = [
    # Anthropic
    ("llm", "anthropic_api_key", ""),
    ("llm", "model", "claude-sonnet-4-6"),
    # Self-hosted OpenAI-compatible (leave empty to use Anthropic)
    ("llm", "llm_host", ""),
    ("llm", "llm_model", ""),
    ("llm", "llm_api_key", ""),
    # Shared
    ("llm", "use_case_system_prompt", _USE_CASE_PROMPT),
    ("llm", "test_case_system_prompt", _TEST_CASE_PROMPT),
    ("llm", "max_tokens", "8192"),
    ("llm", "use_case_temperature", "0"),
    ("llm", "test_case_temperature", "0.2"),
    ("confluence", "url", ""),
    ("confluence", "email", ""),
    ("confluence", "api_token", ""),
    ("clickup", "api_token", ""),
    ("clickup", "use_case_list_id", ""),
    ("clickup", "test_list_id", ""),
    ("clickup", "status", "AI - READY FOR REVIEW"),
]


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category, key)
            );

            CREATE TABLE IF NOT EXISTS run_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_type  INTEGER NOT NULL,
                pipeline_name  TEXT NOT NULL,
                source_info    TEXT,
                status         TEXT DEFAULT 'running',
                output_markdown TEXT,
                run_config     TEXT,
                content_hash   TEXT,
                raw_content    TEXT,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at   TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS use_cases (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER REFERENCES run_history(id) ON DELETE CASCADE,
                prefix_code      TEXT NOT NULL DEFAULT '',
                case_number      TEXT NOT NULL DEFAULT '',
                title            TEXT NOT NULL DEFAULT '',
                priority         TEXT NOT NULL DEFAULT 'Normal',
                original_text    TEXT NOT NULL,
                current_text     TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'Ready for Review',
                rejection_reason TEXT,
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                reviewed_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS test_cases (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER REFERENCES run_history(id) ON DELETE CASCADE,
                prefix_code      TEXT NOT NULL DEFAULT '',
                title            TEXT NOT NULL DEFAULT '',
                use_case_ref     TEXT NOT NULL DEFAULT '',
                use_case_priority TEXT NOT NULL DEFAULT 'Normal',
                original_text    TEXT NOT NULL,
                current_text     TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'Ready for Review',
                rejection_reason TEXT,
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                reviewed_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS test_case_feedback (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                test_case_id     INTEGER REFERENCES test_cases(id) ON DELETE CASCADE,
                feedback_type    TEXT NOT NULL,
                original_text    TEXT,
                revised_text     TEXT,
                rejection_reason TEXT,
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
            );

            CREATE TABLE IF NOT EXISTS use_case_feedback (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                use_case_id      INTEGER REFERENCES use_cases(id) ON DELETE CASCADE,
                feedback_type    TEXT NOT NULL,
                original_text    TEXT,
                revised_text     TEXT,
                rejection_reason TEXT,
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
            );
        """)

        # Migrate: add columns for existing databases
        for col_sql in [
            "ALTER TABLE run_history ADD COLUMN content_hash TEXT",
            "ALTER TABLE run_history ADD COLUMN raw_content TEXT",
            "ALTER TABLE use_cases ADD COLUMN case_number TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE use_cases ADD COLUMN priority TEXT NOT NULL DEFAULT 'Normal'",
            "ALTER TABLE test_cases ADD COLUMN use_case_priority TEXT NOT NULL DEFAULT 'Normal'",
        ]:
            try:
                conn.execute(col_sql)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        for category, key, value in _DEFAULT_SETTINGS:
            conn.execute(
                "INSERT OR IGNORE INTO settings (category, key, value) VALUES (?, ?, ?)",
                (category, key, value),
            )


# ── settings ──────────────────────────────────────────────────────────────────

def get_all_settings() -> dict:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT category, key, value FROM settings ORDER BY category, key"
        ).fetchall()
    result: dict = {}
    for row in rows:
        cat = row["category"]
        if cat not in result:
            result[cat] = {}
        result[cat][row["key"]] = row["value"] or ""
    return result


def update_settings_batch(updates: dict):
    with get_db() as conn:
        for category, keys in updates.items():
            for key, value in keys.items():
                conn.execute(
                    """
                    INSERT INTO settings (category, key, value, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(category, key)
                    DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (category, key, value),
                )


# ── run history ───────────────────────────────────────────────────────────────

def create_run(pipeline_type: int, pipeline_name: str, source_info: str, run_config: str) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO run_history (pipeline_type, pipeline_name, source_info, run_config, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            (pipeline_type, pipeline_name, source_info, run_config),
        )
        return cursor.lastrowid


def update_run(run_id: int, status: str, output_markdown: str = None, pipeline_name: str = None):
    with get_db() as conn:
        if pipeline_name:
            conn.execute(
                """
                UPDATE run_history
                SET status = ?, output_markdown = ?, pipeline_name = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, output_markdown, pipeline_name, run_id),
            )
        else:
            conn.execute(
                """
                UPDATE run_history
                SET status = ?, output_markdown = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, output_markdown, run_id),
            )


def store_run_content(run_id: int, content_hash: str, raw_content: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE run_history SET content_hash = ?, raw_content = ? WHERE id = ?",
            (content_hash, raw_content, run_id),
        )


def get_run(run_id: int):
    with get_db() as conn:
        return conn.execute("SELECT * FROM run_history WHERE id = ?", (run_id,)).fetchone()


def list_runs(limit: int = 100):
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, pipeline_type, pipeline_name, source_info, status, created_at, completed_at
            FROM run_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def delete_run(run_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM run_history WHERE id = ?", (run_id,))


# ── duplicate detection ───────────────────────────────────────────────────────

def find_similar_run(content_hash: str, raw_content: str) -> dict:
    """Check whether similar requirements have been processed before.
    Returns a dict with keys: match ('exact'|'similar'|'none'), run_id, pipeline_name, created_at, similarity.
    """
    with get_db() as conn:
        # Exact hash match first (fast path)
        exact = conn.execute(
            """SELECT id, pipeline_name, created_at FROM run_history
               WHERE content_hash = ? AND status = 'complete'
               ORDER BY created_at DESC LIMIT 1""",
            (content_hash,),
        ).fetchone()
        if exact:
            return {
                "match": "exact",
                "run_id": exact["id"],
                "pipeline_name": exact["pipeline_name"],
                "created_at": exact["created_at"],
                "similarity": 1.0,
            }

        # Fuzzy match against last 20 completed use-case runs that have stored text
        candidates = conn.execute(
            """SELECT id, pipeline_name, created_at, raw_content FROM run_history
               WHERE pipeline_type IN (1, 4) AND status = 'complete' AND raw_content IS NOT NULL
               ORDER BY created_at DESC LIMIT 20""",
        ).fetchall()

    best = None
    best_ratio = 0.0
    for row in candidates:
        ratio = SequenceMatcher(None, raw_content, row["raw_content"] or "").ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = row

    if best and best_ratio >= 0.70:
        return {
            "match": "similar",
            "run_id": best["id"],
            "pipeline_name": best["pipeline_name"],
            "created_at": best["created_at"],
            "similarity": round(best_ratio, 3),
        }

    return {"match": "none"}


# ── individual use cases ──────────────────────────────────────────────────────

def create_use_cases_batch(run_id: int, cases: list) -> list:
    """Insert individual use cases linked to a run. Returns list of inserted IDs."""
    ids = []
    with get_db() as conn:
        for c in cases:
            cursor = conn.execute(
                """INSERT INTO use_cases (run_id, prefix_code, case_number, title, priority, original_text, current_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    c.get("prefix_code", ""),
                    c.get("case_number", ""),
                    c.get("title", ""),
                    c.get("priority", "Normal"),
                    c["original_text"],
                    c["original_text"],
                ),
            )
            ids.append(cursor.lastrowid)
    return ids


def list_use_cases(run_id: int = None, status: str = None) -> list:
    with get_db() as conn:
        clauses = ["WHERE 1=1"]
        params = []
        if run_id is not None:
            clauses.append("AND uc.run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("AND uc.status = ?")
            params.append(status)
        rows = conn.execute(
            f"""SELECT uc.*, rh.pipeline_name, rh.created_at AS run_date
                FROM use_cases uc
                LEFT JOIN run_history rh ON uc.run_id = rh.id
                {' '.join(clauses)}
                ORDER BY uc.id DESC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_use_case(uc_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM use_cases WHERE id = ?", (uc_id,)).fetchone()
        return dict(row) if row else None


def save_use_case_draft(uc_id: int, current_text: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE use_cases SET current_text = ? WHERE id = ?",
            (current_text, uc_id),
        )


def update_use_case_priority(uc_id: int, priority: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE use_cases SET priority = ? WHERE id = ?",
            (priority, uc_id),
        )


def approve_use_case(uc_id: int, current_text: str):
    with get_db() as conn:
        row = conn.execute("SELECT original_text FROM use_cases WHERE id = ?", (uc_id,)).fetchone()
        if not row:
            raise ValueError(f"Use case {uc_id} not found")
        original = row["original_text"]
        was_edited = original.strip() != current_text.strip()
        feedback_type = "approved_with_edits" if was_edited else "approved_clean"
        conn.execute(
            """UPDATE use_cases
               SET current_text = ?, status = 'Approved',
                   reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
               WHERE id = ?""",
            (current_text, uc_id),
        )
        conn.execute(
            """INSERT INTO use_case_feedback (use_case_id, feedback_type, original_text, revised_text)
               VALUES (?, ?, ?, ?)""",
            (uc_id, feedback_type, original, current_text if was_edited else None),
        )


def supersede_previous_use_cases(content_hash: str, current_run_id: int):
    """Mark use cases from earlier runs with the same content hash as Superseded."""
    with get_db() as conn:
        old_runs = conn.execute(
            """SELECT id FROM run_history
               WHERE content_hash = ? AND id != ? AND pipeline_type IN (1, 4)""",
            (content_hash, current_run_id),
        ).fetchall()
        if old_runs:
            placeholders = ",".join("?" * len(old_runs))
            ids = [r["id"] for r in old_runs]
            conn.execute(
                f"""UPDATE use_cases SET status = 'Superseded'
                    WHERE run_id IN ({placeholders}) AND status != 'Superseded'""",
                ids,
            )


def reset_use_case_for_review(uc_id: int, new_text: str):
    """Replace use case text and reset status to Ready for Review after regeneration."""
    with get_db() as conn:
        conn.execute(
            """UPDATE use_cases
               SET current_text = ?, original_text = ?, status = 'Ready for Review',
                   rejection_reason = NULL, reviewed_at = NULL
               WHERE id = ?""",
            (new_text, new_text, uc_id),
        )


def reject_use_case(uc_id: int, reason: str):
    with get_db() as conn:
        row = conn.execute("SELECT original_text FROM use_cases WHERE id = ?", (uc_id,)).fetchone()
        if not row:
            raise ValueError(f"Use case {uc_id} not found")
        conn.execute(
            """UPDATE use_cases
               SET status = 'Rejected', rejection_reason = ?,
                   reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
               WHERE id = ?""",
            (reason, uc_id),
        )
        conn.execute(
            """INSERT INTO use_case_feedback (use_case_id, feedback_type, original_text, rejection_reason)
               VALUES (?, 'rejected', ?, ?)""",
            (uc_id, row["original_text"], reason),
        )


# ── learning context ──────────────────────────────────────────────────────────

def get_learning_context(limit: int = 10) -> str:
    """Return few-shot examples from past human reviews to prepend to the system prompt."""
    with get_db() as conn:
        edits = conn.execute(
            """SELECT original_text, revised_text FROM use_case_feedback
               WHERE feedback_type = 'approved_with_edits' AND revised_text IS NOT NULL
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        rejections = conn.execute(
            """SELECT rejection_reason FROM use_case_feedback
               WHERE feedback_type = 'rejected' AND rejection_reason IS NOT NULL
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    if not edits and not rejections:
        return ""

    parts = ["=== LEARNING CONTEXT FROM PAST HUMAN REVIEWS ===\n"]

    if edits:
        parts.append(
            "The following use cases were edited by a human reviewer before approval. "
            "Apply these patterns to improve your output:\n"
        )
        for i, e in enumerate(edits, 1):
            orig = (e["original_text"] or "")[:500]
            revised = (e["revised_text"] or "")[:500]
            parts.append(f"[Edit Example {i}]\nORIGINAL:\n{orig}\n\nHUMAN APPROVED AS:\n{revised}\n")

    if rejections:
        parts.append(
            "\nThe following rejection reasons were given for past use cases. Avoid repeating these issues:\n"
        )
        for r in rejections:
            parts.append(f"- {r['rejection_reason']}")

    parts.append("\n=== END LEARNING CONTEXT ===\n")
    return "\n".join(parts)


# ── individual test cases ─────────────────────────────────────────────────────

def create_test_cases_batch(run_id: int, cases: list, status: str = "Ready for Review") -> list:
    """Insert individual test cases linked to a run. Returns list of inserted IDs."""
    ids = []
    with get_db() as conn:
        for c in cases:
            cursor = conn.execute(
                """INSERT INTO test_cases
                       (run_id, prefix_code, title, use_case_ref, use_case_priority, original_text, current_text, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    c.get("prefix_code", ""),
                    c.get("title", ""),
                    c.get("use_case_ref", ""),
                    c.get("priority", "Normal"),
                    c["original_text"],
                    c["original_text"],
                    status,
                ),
            )
            ids.append(cursor.lastrowid)
    return ids


def list_test_cases(run_id: int = None, status: str = None) -> list:
    with get_db() as conn:
        clauses = ["WHERE 1=1"]
        params = []
        if run_id is not None:
            clauses.append("AND tc.run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("AND tc.status = ?")
            params.append(status)
        rows = conn.execute(
            f"""SELECT tc.*, rh.pipeline_name, rh.created_at AS run_date
                FROM test_cases tc
                LEFT JOIN run_history rh ON tc.run_id = rh.id
                {' '.join(clauses)}
                ORDER BY tc.id DESC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_test_case(tc_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM test_cases WHERE id = ?", (tc_id,)).fetchone()
        return dict(row) if row else None


def save_test_case_draft(tc_id: int, current_text: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE test_cases SET current_text = ? WHERE id = ?",
            (current_text, tc_id),
        )


def approve_test_case(tc_id: int, current_text: str):
    with get_db() as conn:
        row = conn.execute("SELECT original_text FROM test_cases WHERE id = ?", (tc_id,)).fetchone()
        if not row:
            raise ValueError(f"Test case {tc_id} not found")
        original = row["original_text"]
        was_edited = original.strip() != current_text.strip()
        feedback_type = "approved_with_edits" if was_edited else "approved_clean"
        conn.execute(
            """UPDATE test_cases
               SET current_text = ?, status = 'Approved',
                   reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
               WHERE id = ?""",
            (current_text, tc_id),
        )
        conn.execute(
            """INSERT INTO test_case_feedback (test_case_id, feedback_type, original_text, revised_text)
               VALUES (?, ?, ?, ?)""",
            (tc_id, feedback_type, original, current_text if was_edited else None),
        )


def reject_test_case(tc_id: int, reason: str):
    with get_db() as conn:
        row = conn.execute("SELECT original_text FROM test_cases WHERE id = ?", (tc_id,)).fetchone()
        if not row:
            raise ValueError(f"Test case {tc_id} not found")
        conn.execute(
            """UPDATE test_cases
               SET status = 'Rejected', rejection_reason = ?,
                   reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
               WHERE id = ?""",
            (reason, tc_id),
        )
        conn.execute(
            """INSERT INTO test_case_feedback (test_case_id, feedback_type, original_text, rejection_reason)
               VALUES (?, 'rejected', ?, ?)""",
            (tc_id, row["original_text"], reason),
        )


def delete_use_case(uc_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM use_cases WHERE id = ?", (uc_id,))


def delete_use_cases_bulk(ids: list):
    if not ids:
        return
    with get_db() as conn:
        conn.execute(f"DELETE FROM use_cases WHERE id IN ({','.join('?' * len(ids))})", ids)


def delete_test_case(tc_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM test_cases WHERE id = ?", (tc_id,))


def delete_test_cases_bulk(ids: list):
    if not ids:
        return
    with get_db() as conn:
        conn.execute(f"DELETE FROM test_cases WHERE id IN ({','.join('?' * len(ids))})", ids)


def complete_use_case(uc_id: int):
    with get_db() as conn:
        conn.execute(
            """UPDATE use_cases SET status = 'Complete',
               reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?""",
            (uc_id,),
        )


def complete_test_case(tc_id: int):
    with get_db() as conn:
        conn.execute(
            """UPDATE test_cases SET status = 'Complete',
               reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?""",
            (tc_id,),
        )


def reset_test_case_for_review(tc_id: int, new_text: str):
    """Replace test case text and reset status to Ready for Review after regeneration."""
    with get_db() as conn:
        conn.execute(
            """UPDATE test_cases
               SET current_text = ?, original_text = ?, status = 'Ready for Review',
                   rejection_reason = NULL, reviewed_at = NULL
               WHERE id = ?""",
            (new_text, new_text, tc_id),
        )


def get_test_case_learning_context(limit: int = 10) -> str:
    """Return few-shot examples from past test case reviews to prepend to the system prompt."""
    with get_db() as conn:
        edits = conn.execute(
            """SELECT original_text, revised_text FROM test_case_feedback
               WHERE feedback_type = 'approved_with_edits' AND revised_text IS NOT NULL
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        rejections = conn.execute(
            """SELECT rejection_reason FROM test_case_feedback
               WHERE feedback_type = 'rejected' AND rejection_reason IS NOT NULL
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    if not edits and not rejections:
        return ""

    parts = ["=== LEARNING CONTEXT FROM PAST HUMAN REVIEWS OF TEST CASES ===\n"]

    if edits:
        parts.append(
            "The following test cases were edited by a human reviewer before approval. "
            "Apply these patterns to improve your output:\n"
        )
        for i, e in enumerate(edits, 1):
            orig = (e["original_text"] or "")[:500]
            revised = (e["revised_text"] or "")[:500]
            parts.append(f"[Edit Example {i}]\nORIGINAL:\n{orig}\n\nHUMAN APPROVED AS:\n{revised}\n")

    if rejections:
        parts.append(
            "\nThe following rejection reasons were given for past test cases. Avoid repeating these issues:\n"
        )
        for r in rejections:
            parts.append(f"- {r['rejection_reason']}")

    parts.append("\n=== END LEARNING CONTEXT ===\n")
    return "\n".join(parts)
