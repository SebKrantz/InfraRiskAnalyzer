// The map pop-up escapes everything that comes from the user's file (src/lib/featurePopup.ts).
// No test runner in this repo: Node runs it directly (type stripping, Node 22.18 or
// newer), from frontend/:
//   npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { escapeHtml, formatFeatureProperties } from '../src/lib/featurePopup.ts'

const ATTACK = '<img src=x onerror=alert(1)>'

// Every tag in the pop-up should be one the builder wrote itself: a div or a span.
function foreignTags(html: string): string[] {
  return [...html.matchAll(/<\/?([a-zA-Z][\w-]*)/g)]
    .map((m) => m[1])
    .filter((tag) => tag !== 'div' && tag !== 'span')
}

test('escapeHtml', () => {
  assert.equal(escapeHtml(`a<b>&"'`), 'a&lt;b&gt;&amp;&quot;&#039;')
})

test('a column name cannot inject markup', () => {
  const html = formatFeatureProperties({ [ATTACK]: 1 }, false)
  assert.deepEqual(foreignTags(html), [])
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/)
})

test('a text value cannot inject markup, long or short', () => {
  for (const value of [ATTACK, ATTACK + 'x'.repeat(200)]) {
    assert.deepEqual(foreignTags(formatFeatureProperties({ name: value }, false)), [])
  }
})

test('the fields shown at the top cannot inject markup either', () => {
  const fields = ['affected', 'exposure_level', 'exposure_level_max', 'exposure_level_avg',
    'length_m', 'vulnerability', 'damage_cost', 'damage_cost_lower', 'damage_cost_upper']
  for (const vulnerabilityMode of [false, true]) {
    for (const field of fields) {
      const html = formatFeatureProperties({ [field]: ATTACK }, vulnerabilityMode, 'mm')
      assert.deepEqual(foreignTags(html), [], `${field} (vulnerability mode ${vulnerabilityMode})`)
    }
  }
})

test('ordinary features render as before', () => {
  const html = formatFeatureProperties(
    { name: 'Bridge 1', exposure_level: 1234.4, length_m: 1500, affected: true, road_class: null },
    false,
    'mm'
  )
  for (const text of ['Bridge 1', '1,234 mm', '1.50 km', 'Yes', 'Road class:']) {
    assert.ok(html.includes(text), text)
  }
  const damage = formatFeatureProperties({ damage_cost: 2500000, vulnerability: 0.125 }, true)
  assert.ok(damage.includes('$2,500,000') && damage.includes('12.5%'))
})
