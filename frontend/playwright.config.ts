import { defineConfig, devices } from '@playwright/test';
export default defineConfig({ testDir: './e2e', timeout: 180_000, expect: { timeout: 165_000 }, use: { baseURL: 'http://127.0.0.1:5173', ...devices['Desktop Chrome'] }, workers: 1, webServer: [
  { command: 'python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000', cwd: '..', url: 'http://127.0.0.1:8000/openapi.json', reuseExistingServer: !process.env.CI, timeout: 30_000 },
  { command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: !process.env.CI, timeout: 30_000 },
] });
