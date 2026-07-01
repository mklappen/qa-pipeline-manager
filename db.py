import sqlite3
from contextlib import contextmanager

DB_PATH = "qa_pipeline.db"

_USE_CASE_PROMPT = """\
You are an expert Principal Product Manager and Systems Architect.

Your task is to analyze the provided source data and expand them into a complete \
and detailed set of technical Use Cases.

For each major component or feature requirement discovered, write out three distinct structural paths:
1. Main Success Scenario (Happy Path)
2. Alternative Scenario (Negative/Validation Failure Path)
3. Exception Scenario (Edge Case/System/Network Timeout Path)

CRITICAL FORMATTING RULES:
You must format every single use case block using this EXACT header line pattern so our regex parser can split them:
### USE CASE: [Short Descriptive Feature Title]

Follow that header exactly with these fields:
- **Description:** [Brief summary of interaction objective]
- **Actors:** [User, API Client, Database, Background Worker, etc]
- **Pre-conditions:** [System state required before execution begins]
- **Main Success Scenario Steps:**
  1. [Step 1 description]
  2. [Step 2 description]
- **Alternative Scenario Steps (Negative Path):**
  1. [What happens when validation fails]
- **Exception Scenario Steps (Edge Case):**
  1. [What happens during system crashes or limits]

Output ONLY the markdown use case blocks. Do not add conversational introductions or conclusions.\
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
  "use_case": "[Exact USE CASE title this test belongs to, or empty string]",
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
    ("llm", "temperature", "0.2"),
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
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at   TIMESTAMP
            );
        """)
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
