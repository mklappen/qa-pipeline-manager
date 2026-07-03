import { test, expect } from '@playwright/test';

test.describe('Settings Page', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.click('#nav-settings');
    await expect(page.locator('.section-title')).toContainText('Settings');
  });

  // ── happy path ───────────────────────────────────────────────────────────────

  test('loads with LLM tab active by default', async ({ page }) => {
    await expect(page.locator('#stab-llm')).toHaveClass(/active/);
    await expect(page.locator('#stab-content-llm')).toBeVisible();
    await expect(page.locator('#stab-content-confluence')).toBeHidden();
    await expect(page.locator('#stab-content-clickup')).toBeHidden();
  });

  test('shows Anthropic provider selected by default', async ({ page }) => {
    await expect(page.locator('#provider-anthropic')).toBeChecked();
    await expect(page.locator('#section-anthropic')).toBeVisible();
    await expect(page.locator('#section-self')).toBeHidden();
  });

  test('switches to self-hosted provider and reveals correct fields', async ({ page }) => {
    await page.click('#provider-self');
    await expect(page.locator('#section-self')).toBeVisible();
    await expect(page.locator('#section-anthropic')).toBeHidden();
    await expect(page.locator('#s-llm-llm_host')).toBeVisible();
    await expect(page.locator('#s-llm-llm_model')).toBeVisible();
    await expect(page.locator('#s-llm-llm_api_key')).toBeVisible();
  });

  test('can cycle through all setting tabs', async ({ page }) => {
    await page.click('#stab-confluence');
    await expect(page.locator('#stab-content-confluence')).toBeVisible();
    await expect(page.locator('#stab-content-llm')).toBeHidden();

    await page.click('#stab-clickup');
    await expect(page.locator('#stab-content-clickup')).toBeVisible();
    await expect(page.locator('#stab-content-confluence')).toBeHidden();

    await page.click('#stab-llm');
    await expect(page.locator('#stab-content-llm')).toBeVisible();
    await expect(page.locator('#stab-content-clickup')).toBeHidden();
  });

  test('saves settings and shows success alert', async ({ page }) => {
    await page.fill('#s-llm-max_tokens', '4096');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    await expect(page.locator('#settings-saved-alert')).toContainText('saved successfully');
    // restore
    await page.fill('#s-llm-max_tokens', '8192');
    await page.click('button:has-text("Save All Settings")');
  });

  test('persists model name change across page reload', async ({ page }) => {
    await page.fill('#s-llm-model', 'claude-haiku-4-5-20251001');
    await page.click('button:has-text("Save All Settings")');
    await page.reload();
    await page.click('#nav-settings');
    await expect(page.locator('#s-llm-model')).toHaveValue('claude-haiku-4-5-20251001');
    // restore
    await page.fill('#s-llm-model', 'claude-sonnet-4-6');
    await page.click('button:has-text("Save All Settings")');
  });

  test('persists self-hosted LLM host and auto-selects self-hosted on reload', async ({ page }) => {
    await page.click('#provider-self');
    await page.fill('#s-llm-llm_host', 'http://192.168.1.50:11434/v1');
    await page.fill('#s-llm-llm_model', 'llama3:8b');
    await page.click('button:has-text("Save All Settings")');
    await page.reload();
    await page.click('#nav-settings');
    await expect(page.locator('#provider-self')).toBeChecked();
    await expect(page.locator('#s-llm-llm_host')).toHaveValue('http://192.168.1.50:11434/v1');
    // restore
    await page.fill('#s-llm-llm_host', '');
    await page.click('button:has-text("Save All Settings")');
  });

  test('saves ClickUp default task status', async ({ page }) => {
    await page.click('#stab-clickup');
    await page.fill('#s-clickup-status', 'AI - READY FOR REVIEW');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
  });

  // ── edge cases ───────────────────────────────────────────────────────────────

  test('handles a very long system prompt without error', async ({ page }) => {
    const longPrompt = 'You are an expert QA engineer. '.repeat(200); // ~6000 chars
    await page.fill('#s-llm-use_case_system_prompt', longPrompt);
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    // restore default (just clear - user can reset via UI)
    await page.fill('#s-llm-use_case_system_prompt', '');
    await page.click('button:has-text("Save All Settings")');
  });

  test('handles special characters in the API key field', async ({ page }) => {
    await page.fill('#s-llm-anthropic_api_key', 'sk-ant-api03-abc123!@#$%^&*()');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    // restore
    await page.fill('#s-llm-anthropic_api_key', '');
    await page.click('button:has-text("Save All Settings")');
  });

  test('success alert auto-hides after a few seconds', async ({ page }) => {
    await page.fill('#s-llm-temperature', '0.3');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    await expect(page.locator('#settings-saved-alert')).toBeHidden({ timeout: 5000 });
    // restore
    await page.fill('#s-llm-temperature', '0.2');
    await page.click('button:has-text("Save All Settings")');
  });

  // ── boundary tests ───────────────────────────────────────────────────────────

  test('temperature boundary: accepts minimum value of 0', async ({ page }) => {
    await page.fill('#s-llm-temperature', '0');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    await page.fill('#s-llm-temperature', '0.2');
    await page.click('button:has-text("Save All Settings")');
  });

  test('temperature boundary: accepts maximum value of 1', async ({ page }) => {
    await page.fill('#s-llm-temperature', '1');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    await page.fill('#s-llm-temperature', '0.2');
    await page.click('button:has-text("Save All Settings")');
  });

  test('max tokens boundary: accepts minimum value of 1024', async ({ page }) => {
    await page.fill('#s-llm-max_tokens', '1024');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    await page.fill('#s-llm-max_tokens', '8192');
    await page.click('button:has-text("Save All Settings")');
  });

  test('max tokens boundary: accepts maximum value of 32768', async ({ page }) => {
    await page.fill('#s-llm-max_tokens', '32768');
    await page.click('button:has-text("Save All Settings")');
    await expect(page.locator('#settings-saved-alert')).toBeVisible();
    await page.fill('#s-llm-max_tokens', '8192');
    await page.click('button:has-text("Save All Settings")');
  });
});
