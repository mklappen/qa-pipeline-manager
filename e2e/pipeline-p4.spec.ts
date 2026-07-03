import { test, expect } from '@playwright/test';
import * as path from 'path';
import {
  mockSuccessfulPipelineRun,
  mockFailedPipelineRun,
  MOCK_USE_CASE_MD,
} from './helpers/mocks';

const REQUIREMENTS_TEXT = `# Requirements Specification: Test System Management

## Functional Requirements
### 1. User Authentication
- Users must log in with email and password
- System must support password reset via email

### 2. Dashboard
- Users see a summary of recent test runs
- Pass/fail statistics displayed as charts
`;

const FIXTURES_DIR = path.join(__dirname, 'fixtures');

test.describe('Pipeline 4 — Use Cases from Requirements Text', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.click('#pc-4');
    await expect(page.locator('#pc-4')).toHaveClass(/selected/);
    await expect(page.locator('#form-text')).toBeVisible();
  });

  // ── happy path ───────────────────────────────────────────────────────────────

  test('selects pipeline 4 and shows requirements text form', async ({ page }) => {
    await expect(page.locator('#req-text')).toBeVisible();
    await expect(page.locator('#req-file')).toBeVisible();
    await expect(page.locator('#form-remote')).toBeHidden();
    await expect(page.locator('#push-toggle-wrap')).toBeHidden();
  });

  test('runs pipeline and streams log output', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#output-area')).toBeVisible();
    await expect(page.locator('#log-output')).toContainText('Processing input');
  });

  test('switches to result tab and renders markdown after completion', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
    await expect(page.locator('#result-output h1')).toContainText('System Use Case Specifications');
  });

  test('shows item count badge on result tab', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
    await expect(page.locator('#result-badge')).toBeVisible();
  });

  test('log shows run ID after completion', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#log-output')).toContainText('Run #', { timeout: 10_000 });
  });

  test('run button is disabled while pipeline is running', async ({ page }) => {
    // Delay the stream response so we can observe the disabled state
    await page.route('/api/pipeline/run', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'slow-job' }),
      });
    });
    await page.route('/api/pipeline/stream/slow-job', async route => {
      await new Promise(r => setTimeout(r, 300));
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `data: {"type":"done","run_id":1}\n\n`,
      });
    });
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#run-btn')).toBeDisabled();
  });

  test('clear output button hides output area', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#output-area')).toBeVisible();
    await page.click('#clear-btn');
    await expect(page.locator('#output-area')).toBeHidden();
  });

  test('loads .md file content into requirements textarea', async ({ page }) => {
    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.click('#req-file'),
    ]);
    await fileChooser.setFiles(path.join(FIXTURES_DIR, 'requirements.md'));
    await expect(page.locator('#req-text')).toContainText('Requirements Specification');
  });

  test('system prompt override is accessible via Advanced toggle', async ({ page }) => {
    await expect(page.locator('#adv-section')).toBeHidden();
    await page.click('button:has-text("Advanced")');
    await expect(page.locator('#adv-section')).toBeVisible();
    await expect(page.locator('#sys-prompt-override')).toBeVisible();
  });

  test('uses system prompt override when provided', async ({ page }) => {
    let capturedBody: any;
    await page.route('/api/pipeline/run', async route => {
      capturedBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'override-job' }),
      });
    });
    await page.route('/api/pipeline/stream/override-job', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `data: {"type":"done","run_id":1}\n\n`,
      });
    });
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('button:has-text("Advanced")');
    await page.fill('#sys-prompt-override', 'You are a custom prompt.');
    await page.click('#run-btn');
    await expect.poll(() => capturedBody?.system_prompt_override).toBe('You are a custom prompt.');
  });

  // ── negative tests ───────────────────────────────────────────────────────────

  test('shows alert when Run is clicked with empty requirements text', async ({ page }) => {
    page.on('dialog', async dialog => {
      expect(dialog.message()).toContain('requirements');
      await dialog.accept();
    });
    await page.click('#run-btn');
  });

  test('shows error in log when pipeline returns an error event', async ({ page }) => {
    await mockFailedPipelineRun(page, 'No API key configured.');
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#log-output')).toContainText('ERROR', { timeout: 10_000 });
    await expect(page.locator('#log-output')).toContainText('No API key configured.');
  });

  test('shows HTTP error message when /run endpoint fails', async ({ page }) => {
    await page.route('/api/pipeline/run', async route => {
      await route.fulfill({ status: 400, contentType: 'application/json',
        body: JSON.stringify({ detail: 'Requirements text is required.' }) });
    });
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#log-output')).toContainText('ERROR', { timeout: 5_000 });
  });

  // ── edge cases ───────────────────────────────────────────────────────────────

  test('handles very large requirements text (10,000+ characters)', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    const hugeText = `# Requirements Specification: Large System\n\n` + 'Feature description. '.repeat(500);
    await page.fill('#req-text', hugeText);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
  });

  test('handles requirements text with special characters and markdown', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    const specialText = '# Spec: Special <chars> & "quotes"\n\n- Bullet with `code`\n- Em dash — here';
    await page.fill('#req-text', specialText);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
  });

  // ── boundary tests ───────────────────────────────────────────────────────────

  test('accepts single-character requirements text and submits', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_USE_CASE_MD);
    await page.fill('#req-text', 'A');
    await page.click('#run-btn');
    // Should submit (no client-side length validation), result handled by mock
    await expect(page.locator('#output-area')).toBeVisible();
  });

  test('prefix override is sent in the request payload', async ({ page }) => {
    let capturedBody: any;
    await page.route('/api/pipeline/run', async route => {
      capturedBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'prefix-job' }),
      });
    });
    await page.route('/api/pipeline/stream/prefix-job', async route => {
      await route.fulfill({
        status: 200, contentType: 'text/event-stream',
        body: `data: {"type":"done","run_id":1}\n\n`,
      });
    });
    await page.fill('#req-text', REQUIREMENTS_TEXT);
    await page.fill('#prefix-override', 'XYZ');
    await page.click('#run-btn');
    await expect.poll(() => capturedBody?.prefix_override).toBe('XYZ');
  });
});
