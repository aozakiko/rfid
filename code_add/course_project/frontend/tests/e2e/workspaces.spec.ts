import { test, expect, type APIRequestContext } from '@playwright/test'

async function createTask(request: APIRequestContext, title: string) {
  const response = await request.post('/api/workspaces', {
    data: {
      title,
      scene: 'test',
      task_profile: 'mean_estimation',
      optimization: {
        parents: ['MeasuredHistogramMean', 'MeasuredHistogramMode'],
        baseline_protocol: 'MeasuredHistogramMean',
        iterations: 1,
        evaluation_runs: 1,
        validate_num_tags: 20,
        evaluation_price_max: 8,
      },
      evaluation: {
        sizes: [20],
        distributions: ['uniform'],
        validation_seeds: [74001],
        blind_seeds: [84001],
        perturbations: [{ missing_rate: 0, noise_rate: 0, extreme_rate: 0 }],
      },
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).workspace
}

test('workspace creates a revision in the UI and restores a real preview after refresh', async ({
  page,
  request,
}) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/workspaces')
  await page
    .getByRole('textbox', { name: '研究任务名称', exact: true })
    .fill('browser workspace ' + Date.now())
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/workspaces') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '保存研究任务', exact: true }).click()
  const saved = await saving
  expect(saved.ok()).toBeTruthy()
  const workspace = (await saved.json()).workspace
  await expect(page).toHaveURL(new RegExp('id=' + workspace.id))
  await expect(page.getByRole('button', { name: '预览任务', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '预览任务', exact: true }).click()
  await expect(page.getByRole('heading', { name: '本次研究结果', exact: true })).toBeVisible()
  await expect
    .poll(
      async () =>
        (await (await request.get('/api/workspaces?id=' + workspace.id)).json()).workspace.runs[0]
          .status,
    )
    .toBe('success')
  await page.reload()
  await expect(page.getByRole('textbox', { name: '研究任务名称', exact: true })).toHaveValue(
    workspace.title,
  )
  await expect(page.getByRole('heading', { name: '本次研究结果', exact: true })).toBeVisible()
  await page
    .getByRole('textbox', { name: '研究任务名称', exact: true })
    .fill(workspace.title + ' revised')
  await expect(page.getByRole('button', { name: '预览任务', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '保存配置新版本', exact: true }).click()
  await expect(page.getByText('版本 2', { exact: true })).toBeVisible()
  const exported = await request.get('/api/workspaces/export?id=' + workspace.id)
  const document = await exported.json()
  expect(document.revisions).toHaveLength(2)
  expect(document.runs[0].revision).toBe(1)
  expect(document.runs[0].job.result.dry_run).toBe(true)
  expect(errors).toEqual([])
})

test('workspace runs frozen baseline validation and actual matrix with linked evidence', async ({
  page,
  request,
}) => {
  const workspace = await createTask(request, 'browser matrix ' + Date.now())
  await page.goto('/#/workspaces?id=' + workspace.id)
  await page.getByRole('button', { name: '验证当前赢家或基线', exact: true }).click()
  await expect(page.getByRole('heading', { name: '本次研究结果', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '运行研究跨场景评测', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '运行研究跨场景评测', exact: true }).click()
  await expect(
    page.getByRole('link', { name: '打开完整评测结果与证据 →', exact: true }),
  ).toBeVisible()
  const exported = await (await request.get('/api/workspaces/export?id=' + workspace.id)).json()
  expect(exported.runs.map((run: { status: string }) => run.status)).toEqual(['success', 'success'])
  expect(exported.runs[0].job.result.completed).toBe(2)
  await page.getByRole('link', { name: '打开完整评测结果与证据 →', exact: true }).click()
  await expect(page).toHaveURL(/#\/research\?run=/)
})

test('workspace shows Pareto selection evidence including retained and duplicate decisions', async ({
  page,
  request,
}) => {
  const workspace = await createTask(request, 'browser pareto fixture ' + Date.now())
  const rows = [
    {
      name: 'Accurate',
      retained: true,
      feasible: true,
      reason: 'pareto_front',
      quality_pass_rate: 1,
      quality_score: 1,
      mean_communication_cost_ms: 100,
      pareto_rank: 1,
      crowding_distance: null,
      boundary: true,
      duplicate_of: null,
    },
    {
      name: 'Cheap',
      retained: true,
      feasible: true,
      reason: 'pareto_front',
      quality_pass_rate: 1,
      quality_score: 0.9,
      mean_communication_cost_ms: 30,
      pareto_rank: 1,
      crowding_distance: null,
      boundary: true,
      duplicate_of: null,
    },
    {
      name: 'Copy',
      retained: false,
      feasible: true,
      reason: 'duplicate_structure',
      quality_pass_rate: 1,
      quality_score: 0.9,
      mean_communication_cost_ms: 30,
      pareto_rank: null,
      crowding_distance: null,
      boundary: false,
      duplicate_of: 'Cheap',
    },
  ]
  const job = {
    id: 'offline-pareto-fixture',
    kind: 'auto_iterate',
    status: 'success',
    stage: 'done',
    progress: 100,
    created_at: '2026-11-03T10:00:00',
    elapsed_s: 1,
    payload: {},
    logs: ['offline browser fixture'],
    error: '',
    result: {
      iterations: [],
      stop_reason: 'iterations_completed',
      selection_mode: 'pareto_diversity',
      final_selection: {
        objective: 'pareto_diversity',
        feasible_count: 2,
        duplicates_removed: 1,
        decisions: rows,
        population: ['Accurate', 'Cheap'],
      },
      independent_validation: null,
    },
  }
  await page.route('**/api/workspaces?id=' + workspace.id, (route) =>
    route.fulfill({
      json: {
        workspace: {
          ...workspace,
          runs: [
            {
              id: 'fixture-run',
              revision: 1,
              action: 'optimization',
              created_at: job.created_at,
              job_id: job.id,
              status: 'success',
              job,
              config: {},
            },
          ],
        },
      },
    }),
  )
  await page.goto('/#/workspaces?id=' + workspace.id)
  await expect(
    page.getByRole('img', { name: '质量与通信成本 Pareto 前沿', exact: true }),
  ).toBeVisible()
  await expect(page.getByRole('cell', { name: /源码结构重复/ })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Accurate', exact: true })).toBeVisible()
  await expect(page.locator('.chart canvas')).toHaveCount(1)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2))
    .toBe(true)
})
