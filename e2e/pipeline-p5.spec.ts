import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import {
  mockSuccessfulPipelineRun,
  mockFailedPipelineRun,
  mockHistory,
  mockHistoryItem,
  MOCK_TEST_CASE_MD,
  MOCK_USE_CASE_MD,
  MOCK_HISTORY_USE_CASE_RUN,
} from './helpers/mocks';

const USE_CASE_TEXT = fs.readFileSync(
  path.join(__dirname, 'fixtures', 'use-cases.md'),
  'utf-8'
);

test.describe('Pipeline 5 — Generate Test Cases from Use Cases', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.click('#pc-5');
    await expect(page.locator('#pc-5')).toHaveClass(/selected/);
    await expect(page.locator('#form-usecases')).toBeVisible();
  });

  // ── happy path ───────────────────────────────────────────────────────────────

  test('selects pipeline 5 and shows use case form fields', async ({ page }) => {
    await expect(page.locator('#uc-text')).toBeVisible();
    await expect(page.locator('#uc-history-select')).toBeVisible();
    await expect(page.locator('#form-remote')).toBeHidden();
    await expect(page.locator('#push-toggle-wrap')).toBeHidden();
  });

  test('runs pipeline with pasted use case markdown and streams log', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_TEST_CASE_MD);
    await page.fill('#uc-text', USE_CASE_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#output-area')).toBeVisible();
    await expect(page.locator('#log-output')).toContainText('Processing input');
  });

  test('renders test case markdown in result tab after completion', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_TEST_CASE_MD);
    await page.fill('#uc-text', USE_CASE_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
    await expect(page.locator('#result-output h1')).toContainText('Consolidated Test Specifications');
  });

  test('result tab shows item count badge', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_TEST_CASE_MD);
    await page.fill('#uc-text', USE_CASE_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
    await expect(page.locator('#result-badge')).toBeVisible();
  });

  test('loads use case content from history dropdown', async ({ page }) => {
    await mockHistory(page, [MOCK_HISTORY_USE_CASE_RUN]);
    await mockHistoryItem(page, MOCK_HISTORY_USE_CASE_RUN);

    // Reload so history mocks apply when the dropdown is populated
    await page.goto('/');
    await page.click('#pc-5');

    const select = page.locator('#uc-history-select');
    await expect(select.locator('option')).toHaveCount(2); // blank + 1 run
    await select.selectOption({ index: 1 });

    await expect(page.locator('#uc-text')).toContainText('USE CASE:', { timeout: 5_000 });
  });

  test('sends pipeline_type 5 in the request payload', async ({ page }) => {
    let capturedBody: any;
    await page.route('/api/pipeline/run', async route => {
      capturedBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ job_id: 'p5-job' }),
      });
    });
    await page.route('/api/pipeline/stream/p5-job', async route => {
      await route.fulfill({
        status: 200, contentType: 'text/event-stream',
        body: `data: {"type":"done","run_id":1}\n\n`,
      });
    });
    await page.fill('#uc-text', USE_CASE_TEXT);
    await page.click('#run-btn');
    await expect.poll(() => capturedBody?.pipeline_type).toBe(5);
  });

  test('sends system prompt override when provided via Advanced section', async ({ page }) => {
    let capturedBody: any;
    await page.route('/api/pipeline/run', async route => {
      capturedBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ job_id: 'override-job' }),
      });
    });
    await page.route('/api/pipeline/stream/override-job', async route => {
      await route.fulfill({
        status: 200, contentType: 'text/event-stream',
        body: `data: {"type":"done","run_id":1}\n\n`,
      });
    });
    await page.fill('#uc-text', USE_CASE_TEXT);
    await page.click('button:has-text("Advanced")');
    await page.fill('#sys-prompt-override', 'Custom test case instructions.');
    await page.click('#run-btn');
    await expect.poll(() => capturedBody?.system_prompt_override).toBe('Custom test case instructions.');
  });

  // ── negative tests ───────────────────────────────────────────────────────────

  test('shows alert when Run is clicked with no text and no history selection', async ({ page }) => {
    page.on('dialog', async dialog => {
      expect(dialog.message()).toMatch(/use case|text|select/i);
      await dialog.accept();
    });
    await page.click('#run-btn');
  });

  test('shows error in log when backend cannot find ### USE CASE: headers', async ({ page }) => {
    await mockFailedPipelineRun(page, 'No use cases (### USE CASE: headers) found in the provided text.');
    await page.fill('#uc-text', 'This is plain text with no use case headers.');
    await page.click('#run-btn');
    await expect(page.locator('#log-output')).toContainText('### USE CASE:', { timeout: 10_000 });
  });

  test('shows error in log when pipeline fails', async ({ page }) => {
    await mockFailedPipelineRun(page, 'LLM host unreachable.');
    await page.fill('#uc-text', USE_CASE_TEXT);
    await page.click('#run-btn');
    await expect(page.locator('#log-output')).toContainText('ERROR', { timeout: 10_000 });
    await expect(page.locator('#log-output')).toContainText('LLM host unreachable.');
  });

  // ── edge cases ───────────────────────────────────────────────────────────────

  test('handles use case text with only one use case block', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_TEST_CASE_MD);
    const singleUC = `# System Use Case Specifications (SNG)\n\n## 1. [UC - SNG] - Single Feature\n\n### USE CASE: Single Feature\n- **Description:** Only one use case\n`;
    await page.fill('#uc-text', singleUC);
    await page.click('#run-btn');
    await expect(page.locator('#tab-result')).toHaveClass(/active/, { timeout: 10_000 });
  });

  test('handles use case text with unicode and emoji characters', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_TEST_CASE_MD);
    const unicodeUC = `### USE CASE: Localisation — UTF-8 Support 🌍\n- **Description:** Handles ñ, ü, 中文, العربية\n`;
    await page.fill('#uc-text', unicodeUC);
    await page.click('#run-btn');
    await expect(page.locator('#output-area')).toBeVisible();
  });

  // ── boundary tests ────────────────────────────────────────────────────────────

  test('accepts use case text with exactly one ### USE CASE: header and no other content', async ({ page }) => {
    await mockSuccessfulPipelineRun(page, MOCK_TEST_CASE_MD);
    const minimalUC = `### USE CASE: Minimal\n`;
    await page.fill('#uc-text', minimalUC);
    await page.click('#run-btn');
    await expect(page.locator('#output-area')).toBeVisible();
  });
});
