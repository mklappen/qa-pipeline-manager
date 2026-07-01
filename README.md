# QA Pipeline Manager

An AI-powered QA automation tool that generates **Use Cases** and **Test Cases** from requirements documents, Confluence pages, and ClickUp tasks — and can push results directly back to ClickUp.

Built with FastAPI, SQLite, and the Anthropic Claude API (with support for self-hosted LLMs).

---

## Features

- Generate structured Use Cases from Confluence pages, ClickUp tasks, or pasted/uploaded requirements
- Generate QA Test Cases from Use Cases or directly from requirements
- Push generated Use Cases and Test Cases directly to ClickUp
- Browser-based UI with live streaming output as the AI generates results
- Full run history with markdown rendering
- All settings (API keys, model, system prompts) stored in the app — no hardcoding
- Supports Anthropic Claude or any self-hosted OpenAI-compatible LLM (Ollama, vLLM, LM Studio, etc.)
- Upload requirements as `.txt`, `.md`, `.pdf`, or `.docx`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | [FastAPI](https://fastapi.tiangolo.com/) (Python) |
| Database | SQLite (via Python's built-in `sqlite3`) |
| AI / LLM | [Anthropic Claude API](https://docs.anthropic.com/) or self-hosted OpenAI-compatible endpoint |
| Streaming | Server-Sent Events (SSE) |
| Frontend | Vanilla HTML/CSS/JS — Bootstrap 5, marked.js |
| HTTP client | httpx (async) |
| Document parsing | pypdf (PDF), python-docx (DOCX) |
| Project management | ClickUp API v2 |
| Knowledge base | Confluence API v2 |

---

## Project Structure

```
server.py           ← FastAPI entry point
db.py               ← SQLite database layer (settings + run history)
core/
  llm.py            ← Unified LLM caller (Anthropic or self-hosted)
  use_cases.py      ← Use case generation logic
  tests.py          ← Test case generation + JSON parsing
routes/
  settings.py       ← GET/PUT /api/settings
  history.py        ← GET/DELETE /api/history
  pipeline.py       ← Pipeline execution with SSE streaming
  upload.py         ← File text extraction endpoint
static/
  index.html        ← Full single-page app (no build step)
requirements.txt
```

---

## Setup

### Requirements

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/) (or a self-hosted LLM endpoint)

### Install

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### Configure

No config files needed — all settings are managed inside the app under the **Settings** page after first launch.

Optionally, create a `.env` file if you prefer to set the Anthropic API key as an environment variable:

```
ANTHROPIC_API_KEY=sk-ant-...
```

If you enter the API key in **Settings → LLM**, the `.env` file is not required.

### Run

```bash
python server.py
```

Open **http://127.0.0.1:8000** in your browser.

---

## First-Time Configuration

On first run, navigate to **Settings** and fill in:

**LLM tab**
- Choose provider: **Anthropic Claude** or **Self-hosted**
- Enter your API key and model name
- Optionally adjust Max Tokens, Temperature, and system prompts

**Confluence tab** *(optional)*
- Base URL, email, and API token for your Atlassian instance

**ClickUp tab** *(optional)*
- API token, Use Case list ID, Test Case list ID, and default task status

---

## Pipelines

| # | Pipeline | Description |
|---|---|---|
| 1 | Use Cases from Confluence / ClickUp | Fetch a Confluence page and/or ClickUp task, generate structured Use Cases |
| 2 | Tests from Approved ClickUp Use Cases | Pull tasks with status `Approved` from your ClickUp Use Case list, generate Test Cases |
| 3 | Tests from Confluence / ClickUp | Generate Test Cases directly from requirements, skipping the Use Case step |
| 4 | Use Cases from Requirements Text | Paste or upload a requirements document (.txt, .md, .pdf, .docx) and generate Use Cases |
| 5 | Generate Test Cases from Use Cases | Take a previous Use Case run (from history) and generate Test Cases from it |

Each pipeline streams live log output to the browser and saves the result to history.

---

## Self-hosted LLM

Any OpenAI-compatible endpoint works. In **Settings → LLM**, select **Self-hosted** and enter:

- **Host URL** — e.g. `http://192.168.1.100:11434/v1` (Ollama) or your vLLM/LM Studio address
- **Model name** — e.g. `google/gemma-3-27b-it`
- **API key** — leave blank if not required

---

## Notes

- The SQLite database (`qa_pipeline.db`) is created automatically on first run
- All credentials entered in Settings are stored in the local database — keep the database file secure
- The database file is excluded from git via `.gitignore`
