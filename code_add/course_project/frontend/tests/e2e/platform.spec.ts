import { test, expect } from '@playwright/test'

test('all Vue routes load against the real backend without browser exceptions', async ({
  page,
}) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '研究总览', exact: true })).toBeVisible()
  for (const [path, title] of [
    ['assets', '场景与代码'],
    ['design', '协议设计'],
    ['validation', '验证沙箱'],
    ['optimization', '自动迭代'],
    ['experiments', '实验分析'],
    ['statistics', '统计基准'],
    ['artifacts', '实验产物'],
    ['audit', '运行审计'],
    ['research', '候选评测'],
  ]) {
    await page.goto('/#/' + path)
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await expect(page.locator('main h2').first()).toBeVisible()
  }
  await expect(page.getByText('任务执行失败，请查看服务日志。', { exact: true })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('strict verification uses actual platform channel and complete results', async ({ page }) => {
  await page.goto('/#/validation')
  await expect(page.getByRole('heading', { name: '验证配置' })).toBeVisible()
  await page.getByRole('combobox', { name: '任务画像', exact: true }).press('ArrowDown')
  await page.getByRole('option', { name: '连续数值均值估计', exact: true }).click()
  await page.getByRole('combobox', { name: '待验证协议', exact: true }).click()
  await page.getByRole('option', { name: 'MeasuredHistogramMean', exact: true }).click()
  await page.getByRole('button', { name: '验证协议', exact: true }).click()
  await expect(page.getByText('已完成执行', { exact: true })).toBeVisible({ timeout: 30000 })
  await expect(page.getByText('instrumented_channel', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('3 / 3', { exact: true })).toBeVisible()
})

test('Vue iteration preview calls actual background workflow without model credentials', async ({
  page,
}) => {
  await page.goto('/#/optimization')
  await expect(page.getByRole('heading', { name: '优化任务' })).toBeVisible()
  await page.getByLabel('只预览任务', { exact: true }).check()
  await page.getByLabel('新协议前缀', { exact: true }).fill('vue_browser_preview')
  await page.getByRole('button', { name: '启动迭代预览', exact: true }).click()
  await expect(page.getByTestId('optimization-result')).toBeVisible({ timeout: 30000 })
  await expect(
    page.getByText('只生成 Prompt，不调用模型或执行候选评价。', { exact: true }),
  ).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'vue_browser_preview', exact: true }).first().click()
  await expect(page.getByTestId('optimization-result')).toBeVisible()
})

test('new client remains usable on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '研究总览', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '展开导航' }).click()
  await page.getByRole('link', { name: '05 自动迭代' }).click()
  await expect(page.getByRole('heading', { name: '自动迭代', exact: true })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2)
  expect(overflow).toBe(false)
})

test('file conflict keeps unsaved edits and navigation can be cancelled', async ({ page }) => {
  let saves = 0
  await page.route('**/api/selfopt/file**', async (route) => {
    if (route.request().method() === 'POST') {
      saves++
      await route.fulfill({ status: 409, json: { error: '磁盘版本已变化，请重新读取' } })
    } else
      await route.fulfill({
        json: {
          path: 'fixture/Task.py',
          revision: 'fixture-revision',
          content: '# original fixture',
        },
      })
  })
  await page.goto('/#/assets')
  await page.getByRole('button', { name: 'Task.py', exact: true }).click()
  const editor = page.getByRole('textbox', { name: '源码编辑器', exact: true })
  await expect(editor).toHaveValue('# original fixture')
  await editor.fill('# unsaved fixture')
  await page.getByRole('button', { name: '保存文件', exact: true }).click()
  await expect(page.getByText('磁盘版本已变化，请重新读取', { exact: true })).toBeVisible()
  await expect(editor).toHaveValue('# unsaved fixture')
  await page.getByRole('link', { name: '01 研究总览' }).click()
  await page.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page).toHaveURL(/#\/assets$/)
  await expect(editor).toHaveValue('# unsaved fixture')
  await page.getByRole('link', { name: '01 研究总览' }).click()
  await page.getByRole('button', { name: '放弃修改', exact: true }).click()
  await expect(page.getByRole('heading', { name: '研究总览', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '02 场景与代码' }).click()
  await expect(editor).toHaveValue('# original fixture')
  expect(saves).toBe(1)
})

test('accepted background work can be stopped and restored from server history', async ({
  page,
}) => {
  let job = {
    id: 'vue-fixture-cancel',
    kind: 'auto_iterate',
    status: 'running',
    stage: 'model',
    progress: 35,
    elapsed_s: 1,
    created_at: '2026-11-03T10:00:00',
    payload: {},
    logs: ['fixture accepted'],
    error: '',
    result: null,
  }
  await page.route('**/api/selfopt/jobs', (route) => route.fulfill({ json: { jobs: [job] } }))
  await page.route('**/api/selfopt/job?**', (route) => route.fulfill({ json: { job } }))
  await page.route('**/api/selfopt/job/cancel', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ id: job.id })
    job = { ...job, status: 'cancelled', logs: ['fixture stopped'] }
    await route.fulfill({ json: { job } })
  })
  await page.goto('/#/audit')
  await page.getByRole('cell', { name: job.id, exact: true }).click()
  await page.getByRole('button', { name: '停止任务', exact: true }).click()
  await expect(page.getByRole('log')).toHaveText('fixture stopped')
  await expect(page.getByTestId('active-jobs')).toHaveText('0 个进行中任务')
  await page.reload()
  await page.getByRole('cell', { name: job.id, exact: true }).click()
  await expect(page.getByRole('log')).toHaveText('fixture stopped')
  await expect(page.getByRole('button', { name: '停止任务', exact: true })).toHaveCount(0)
})

test('model key is shared across routes only in memory and cleared on reload', async ({ page }) => {
  await page.goto('/#/design')
  await page
    .getByRole('textbox', { name: 'API URL', exact: true })
    .fill('https://fixture.invalid/v1')
  await page.getByLabel('API Key', { exact: true }).fill('vue-fixture-secret')
  const model = page.getByRole('combobox', { name: '模型名称', exact: true })
  await model.fill('fixture-model')
  await page.getByRole('option', { name: 'fixture-model', exact: true }).click()
  await page.getByRole('link', { name: '05 自动迭代' }).click()
  await expect(page.getByText('fixture-model', { exact: true })).toBeVisible()
  const storage = await page.evaluate(() =>
    JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }),
  )
  expect(storage).not.toContain('vue-fixture-secret')
  await page.getByRole('link', { name: '03 协议设计' }).click()
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('vue-fixture-secret')
  await page.reload()
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
})

test('asset preview renders image suffixes and CSV pages from the server', async ({ page }) => {
  await page.route('**/api/selfopt/state', async (route) => {
    const state = await (await route.fetch()).json()
    state.experiment_artifacts = []
    state.output_artifacts = [
      { name: 'fixture-chart.png', path: 'fixture/chart.png', size: 10 },
      { name: 'fixture-records.csv', path: 'fixture/records.csv', size: 200 },
    ]
    await route.fulfill({ json: state })
  })
  await page.route('**/api/selfopt/preview?**', (route) => {
    const url = new URL(route.request().url())
    return route.fulfill({
      json: url.searchParams.get('path')?.endsWith('.png')
        ? { kind: 'png', name: 'fixture-chart.png' }
        : {
            kind: 'csv',
            name: 'fixture-records.csv',
            headers: ['seed', 'result'],
            rows: [[url.searchParams.get('page') === '2' ? '21' : '1', 'passed']],
            total: 21,
            page_size: 20,
          },
    })
  })
  await page.route('**/api/selfopt/raw?**', (route) =>
    route.fulfill({
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==',
        'base64',
      ),
    }),
  )
  await page.goto('/#/artifacts')
  await page.getByRole('button', { name: 'fixture-chart.png', exact: true }).click()
  const image = page.getByRole('img', { name: 'fixture-chart.png', exact: true })
  await expect(image).toBeVisible()
  await expect
    .poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth))
    .toBeGreaterThan(0)
  await page.getByRole('button', { name: 'fixture-records.csv', exact: true }).click()
  const preview = page
    .locator('section.panel')
    .filter({ has: page.getByText('fixture-records.csv', { exact: true }) })
    .last()
  await expect(preview.getByRole('cell', { name: 'passed', exact: true })).toBeVisible()
  await preview.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(preview.getByRole('cell', { name: '21', exact: true })).toBeVisible()
})

test('configured experiment runs through the real queue and produces audited evidence', async ({
  page,
}) => {
  await page.goto('/#/experiments')
  await page.getByRole('textbox', { name: '实验名称', exact: true }).fill('vue-browser-smoke')
  await page.getByRole('textbox', { name: '协议（逗号分隔）', exact: true }).fill('shared_sketch')
  await page.getByRole('textbox', { name: '开发 Seeds', exact: true }).fill('9311701')
  await page.getByRole('textbox', { name: '标签规模', exact: true }).fill('32')
  await page.getByRole('button', { name: '运行配置实验', exact: true }).click()
  await expect(
    page.getByRole('region', { name: '任务进度' }).getByText('已完成', { exact: true }),
  ).toBeVisible({ timeout: 30000 })
  await expect(page.getByText('产物审计通过', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '从原始结果重建报告', exact: true })).toBeEnabled()
})

test('frozen candidate flows through validation and a real independent matrix', async ({
  page,
  request,
}) => {
  const registration = await request.post('/api/research/register', {
    data: {
      family: 'python',
      scene: 'multi_mode',
      protocol: 'MeasuredHistogramMean',
      task_profile: 'mean_estimation',
    },
  })
  expect(registration.status()).toBe(201)
  const candidate = (await registration.json()).candidate
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/research?candidate=' + candidate.id)
  const candidateRow = page
    .getByRole('row')
    .filter({ has: page.getByText(candidate.id.slice(0, 12), { exact: false }) })
    .first()
  await candidateRow.getByRole('button', { name: '验证此候选', exact: true }).click()
  await expect(page).toHaveURL(/#\/validation\?candidate=/)
  await expect(page.getByText('使用冻结版本', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '验证协议', exact: true }).click()
  await expect(page.getByText('已完成执行', { exact: true })).toBeVisible({ timeout: 30000 })
  await expect(page.getByText('3 / 3', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '加入对照实验', exact: true }).click()
  await expect(page.getByRole('heading', { name: '候选评测', exact: true })).toBeVisible()
  await page.getByLabel('标签规模', { exact: true }).fill('32')
  await page.getByLabel('独立验证 Seeds', { exact: true }).fill('9511701')
  await page.getByLabel('盲测 Seeds', { exact: true }).fill('9521701')
  await page.getByText('同时运行缺失 / 噪声 / 异常扰动', { exact: true }).click()
  await expect(
    page.getByRole('checkbox', { name: '同时运行缺失 / 噪声 / 异常扰动', exact: true }),
  ).not.toBeChecked()
  await page.getByRole('button', { name: '冻结配置并运行评测', exact: true }).click()
  await expect(page.getByText('与已记录搜索及验证数据无重叠', { exact: true })).toBeVisible({
    timeout: 30000,
  })
  await expect(page.getByText('文件审计通过', { exact: false })).toBeVisible()
  await expect(
    page.getByRole('img', { name: '冻结候选跨场景通信成本对照', exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'summary.csv', exact: true }).click()
  await expect(page.getByRole('link', { name: '下载文件 ↓', exact: true })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: '下载文件 ↓', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('summary.csv')
  await page.reload()
  const history = page.getByRole('combobox', { name: '历史候选评测', exact: true })
  await history.press('ArrowDown')
  await page.getByRole('option').first().click()
  await expect(page.getByText('文件审计通过', { exact: false })).toBeVisible()
  const runs = await (await request.get('/api/research/runs')).json()
  await page.goto('/#/research?candidate=' + candidate.id + '&run=' + runs.runs[0].run_id)
  await expect(page.getByText('文件审计通过', { exact: false })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2)).toBe(
    false,
  )
  expect(errors).toEqual([])
})

test('validation shows actionable structured background failures', async ({ page }) => {
  const job = {
    id: 'vue-validation-failure',
    kind: 'protocol_validation',
    status: 'failed',
    stage: 'failed',
    progress: 100,
    elapsed_s: 1,
    created_at: '2026-11-03T12:00:00',
    payload: {},
    logs: ['timed out safely'],
    error: '运行超过时间预算。',
    result: { partial: true, completed_runs: 1 },
    error_info: {
      code: 'timeout',
      stage: 'validation',
      message: '运行超过时间预算。',
      suggestion: '减少重复次数或增加预算后重试。',
      retryable: true,
      request_id: 'vue-error-id',
    },
  }
  await page.route('**/api/selfopt/validate-async', (route) =>
    route.fulfill({ status: 202, json: { job } }),
  )
  await page.route('**/api/selfopt/job?**', (route) => route.fulfill({ json: { job } }))
  await page.goto('/#/validation')
  await page.getByRole('button', { name: '验证协议', exact: true }).click()
  await expect(page.getByText('减少重复次数或增加预算后重试。', { exact: true })).toBeVisible()
  await expect(page.getByText('失败阶段 validation', { exact: false })).toBeVisible()
  await expect(page.getByText('vue-error-id', { exact: false })).toBeVisible()
  await expect(page.getByText('已保留部分结果', { exact: false })).toBeVisible()
})

test('candidate evaluation remains usable on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/research')
  await expect(page.getByRole('heading', { name: '跨场景验证与盲测', exact: true })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2)
  expect(overflow).toBe(false)
})
