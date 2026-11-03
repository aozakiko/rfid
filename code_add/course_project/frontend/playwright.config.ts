import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 45000,
  use: {
    baseURL: process.env.RFID_TEST_URL || 'http://127.0.0.1:8765',
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  workers: 1,
  reporter: 'list',
})
