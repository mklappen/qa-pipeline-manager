import { Page } from '@playwright/test';

export const MOCK_JOB_ID = 'test-job-abc123';
export const MOCK_RUN_ID = 999;

// ── sample markdown payloads ──────────────────────────────────────────────────

export const MOCK_USE_CASE_MD = `# System Use Case Specifications (TSM)

## 1. [UC - TSM] - User Login

### USE CASE: User Login
- **Description:** User accesses the system via email and password
- **Actors:** User, Authentication Service
- **Pre-conditions:** User has a registered account
- **Main Success Scenario Steps:**
  1. User navigates to login page
  2. User enters valid credentials
  3. System authenticates and redirects to dashboard
- **Alternative Scenario Steps (Negative Path):**
  1. User enters invalid credentials
  2. System displays error message
- **Exception Scenario Steps (Edge Case):**
  1. Auth service unavailable
  2. System shows maintenance message

---
`;

export const MOCK_TEST_CASE_MD = `# Consolidated Test Specifications (TSM)

## 1. [TSM] - User Login - Happy Path

**USE CASE:** User Login

**Pre-conditions:**
- User has a registered account
- System is online

**Test Steps:**
1. Navigate to login page
2. Enter valid email and password
3. Click Login

**Expected Result:**
- User is redirected to dashboard

---

## 2. [TSM] - User Login - Invalid Credentials

**USE CASE:** User Login

**Pre-conditions:**
- System is online

**Test Steps:**
1. Navigate to login page
2. Enter invalid password
3. Click Login

**Expected Result:**
- Error message is displayed
- User remains on login page

---
`;

// ── mock history rows ──────────────────────────────────────────────────────────

export const MOCK_HISTORY_USE_CASE_RUN = {
  id: 901,
  pipeline_type: 4,
  pipeline_name: 'Create Use Cases from Requirements file or text',
  source_info: '{}',
  status: 'complete',
  created_at: '2026-06-30T10:00:00',
  completed_at: '2026-06-30T10:01:30',
  output_markdown: MOCK_USE_CASE_MD,
};

export const MOCK_HISTORY_TEST_CASE_RUN = {
  id: 902,
  pipeline_type: 5,
  pipeline_name: 'Test Cases - TSM',
  source_info: '{}',
  status: 'complete',
  created_at: '2026-06-30T10:05:00',
  completed_at: '2026-06-30T10:06:45',
  output_markdown: MOCK_TEST_CASE_MD,
};

export const MOCK_HISTORY_ERROR_RUN = {
  id: 903,
  pipeline_type: 4,
  pipeline_name: 'Create Use Cases from Requirements file or text',
  source_info: '{}',
  status: 'error',
  created_at: '2026-06-30T09:00:00',
  completed_at: '2026-06-30T09:00:05',
  output_markdown: 'Error: API key not configured.',
};

// ── pipeline mock helpers ─────────────────────────────────────────────────────

export async function mockSuccessfulPipelineRun(page: Page, resultMd: string) {
  await page.route('/api/pipeline/run', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: MOCK_JOB_ID }),
    });
  });

  await page.route(`/api/pipeline/stream/${MOCK_JOB_ID}`, async route => {
    const events = [
      `data: ${JSON.stringify({ type: 'run_id', run_id: MOCK_RUN_ID })}\n\n`,
      `data: ${JSON.stringify({ type: 'log', message: 'Processing input...' })}\n\n`,
      `data: ${JSON.stringify({ type: 'log', message: 'Sending to Claude (claude-sonnet-4-6)...' })}\n\n`,
      `data: ${JSON.stringify({ type: 'log', message: 'Generated 2 items.' })}\n\n`,
      `data: ${JSON.stringify({ type: 'result', markdown: resultMd, run_id: MOCK_RUN_ID })}\n\n`,
      `data: ${JSON.stringify({ type: 'done', run_id: MOCK_RUN_ID })}\n\n`,
    ].join('');

    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: events,
    });
  });
}

export async function mockFailedPipelineRun(page: Page, errorMessage: string) {
  await page.route('/api/pipeline/run', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: MOCK_JOB_ID }),
    });
  });

  await page.route(`/api/pipeline/stream/${MOCK_JOB_ID}`, async route => {
    const events = [
      `data: ${JSON.stringify({ type: 'run_id', run_id: MOCK_RUN_ID })}\n\n`,
      `data: ${JSON.stringify({ type: 'error', message: errorMessage, run_id: MOCK_RUN_ID })}\n\n`,
    ].join('');

    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: events,
    });
  });
}

export async function mockHistory(page: Page, runs: object[]) {
  await page.route('/api/history', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(runs),
      });
    } else {
      await route.continue();
    }
  });
}

export async function mockHistoryItem(page: Page, run: object) {
  const id = (run as any).id;
  await page.route(`/api/history/${id}`, async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(run),
    });
  });
}
