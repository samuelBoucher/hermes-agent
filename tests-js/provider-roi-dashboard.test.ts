import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'

import { test } from 'vitest'

const DASHBOARD_PATH = path.resolve(__dirname, '..', 'plugins', 'provider-roi', 'dashboard', 'dist', 'index.js')

type Child = VNode | string | number | boolean | null | undefined | Child[]

interface VNode {
  type: unknown
  props: Record<string, unknown>
}

interface Provider {
  provider: string
  rule: {
    label?: string
    included: boolean
    classification: string
    plan?: { monthly_cost_cad?: number }
  }
  activity: number
  coverage: string
  verdict: { action: string; reason: string }
  profiles: string[]
  models: unknown[]
  usage_cost_usd: number
  cost_status: string
  plan_configured: boolean
  metered_cost_status?: string
}

function isVNode(value: Child): value is VNode {
  return typeof value === 'object' && value !== null && 'type' in value && 'props' in value
}

function children(node: VNode): Child[] {
  const value = node.props.children
  return Array.isArray(value) ? value as Child[] : [value as Child]
}

function textContent(value: Child): string {
  if (Array.isArray(value)) return value.map(textContent).join('')
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (!isVNode(value)) return ''
  return children(value).map(textContent).join('')
}

function findVNode(value: Child, predicate: (node: VNode) => boolean): VNode | undefined {
  if (Array.isArray(value)) {
    for (const child of value) {
      const match = findVNode(child, predicate)
      if (match) return match
    }
    return undefined
  }
  if (!isVNode(value)) return undefined
  if (predicate(value)) return value
  for (const child of children(value)) {
    const match = findVNode(child, predicate)
    if (match) return match
  }
  return undefined
}

function loadPage(): { page: (props: Record<string, never>) => VNode; setStateValues: (values: unknown[]) => void } {
  let registered: ((props: Record<string, never>) => VNode) | undefined
  let stateValues: unknown[] = []
  const React = {
    Fragment: Symbol('Fragment'),
    createElement(type: unknown, props: Record<string, unknown> | null, ...childValues: Child[]): VNode {
      return { type, props: { ...(props ?? {}), children: childValues } }
    },
  }
  const sdk = {
    React,
    components: {
      Card: 'Card', CardHeader: 'CardHeader', CardTitle: 'CardTitle', CardContent: 'CardContent',
      Badge: 'Badge', Button: 'Button', Input: 'Input', Label: 'Label', Select: 'Select',
      SelectOption: 'SelectOption', Separator: 'Separator',
    },
    hooks: {
      useState(initial: unknown): [unknown, () => void] {
        return [stateValues.length ? stateValues.shift() : initial, () => {}]
      },
      useEffect(): void {},
      useCallback(callback: unknown): unknown { return callback },
      useMemo(callback: () => unknown): unknown { return callback() },
    },
    fetchJSON(): Promise<never> { return Promise.reject(new Error('not invoked by render harness')) },
  }
  const window = {
    __HERMES_PLUGIN_SDK__: sdk,
    __HERMES_PLUGINS__: {
      register(name: string, page: (props: Record<string, never>) => VNode): void {
        assert.equal(name, 'provider-roi')
        registered = page
      },
    },
  }

  vm.runInNewContext(fs.readFileSync(DASHBOARD_PATH, 'utf8'), { Intl, window })
  assert.ok(registered, 'provider ROI dashboard must register its page')
  return { page: registered, setStateValues: values => { stateValues = [...values] } }
}

function renderProviderRow(provider: Provider, expanded: boolean): VNode {
  const dashboard = loadPage()
  const report = { providers: [provider], summary: {}, warnings: [] }
  dashboard.setStateValues([report])
  const page = dashboard.page({})
  const rowElement = findVNode(page, node => typeof node.type === 'function' && (node.type as Function).name === 'ProviderRow')
  assert.ok(rowElement, 'page must render a ProviderRow for each provider')
  dashboard.setStateValues([expanded])
  return (rowElement.type as (props: Record<string, unknown>) => VNode)(rowElement.props)
}

function provider(overrides: Partial<Provider> = {}): Provider {
  return {
    provider: 'legacy-payg',
    rule: { label: 'Legacy PAYG', included: true, classification: 'payg' },
    activity: 10,
    coverage: 'complete',
    verdict: { action: 'keep', reason: 'test fixture' },
    profiles: [],
    models: [],
    usage_cost_usd: 0,
    cost_status: 'estimated',
    plan_configured: false,
    ...overrides,
  }
}

test('legacy estimated zero cost without a metered status is not rendered as money', () => {
  const row = renderProviderRow(provider(), true)
  const rendered = textContent(row)

  assert.match(rendered, /N\/A — cost not reported/)
  assert.doesNotMatch(rendered, /\$0\.00/)
})

test('provider row keeps six labelled grid cells and nests the plan badge with the provider toggle', () => {
  const row = renderProviderRow(provider({
    plan_configured: true,
    rule: { label: 'Legacy PAYG', included: true, classification: 'payg', plan: { monthly_cost_cad: 0 } },
  }), false)
  const grid = findVNode(row, node => node.props.className === 'provider-roi-row')
  assert.ok(grid, 'provider row must include its comparison grid')
  const cells = children(grid).filter((child): child is VNode => isVNode(child))

  assert.equal(cells.length, 6)
  assert.deepEqual(cells.map(cell => cell.props['data-label']), [
    'Provider', 'Activity', 'Kanban', 'Coverage', 'Verdict', 'Plan',
  ])
  assert.match(textContent(cells[0]), /Legacy PAYGPlan: 0 CAD\/mo/)
  assert.equal(cells[5].props['data-label'], 'Plan')
  assert.match(textContent(cells[5]), /Exclude/)
})
