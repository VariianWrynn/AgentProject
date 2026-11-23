import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true } })
try {
  const { EvidenceMarkdown } = await server.ssrLoadModule('/src/components/EvidenceMarkdown.tsx')
  const { ChartView } = await server.ssrLoadModule('/src/components/ChartView.tsx')
  const { ReportView } = await server.ssrLoadModule('/src/components/ReportView.tsx')
  const references = [
    { label: 'E1', title: '研究资料', text: '资料原文 <script>不能执行</script>', url: 'https://example.org/source', date: '2026-11-19' },
    { label: 'D1', title: '营收数据', text: 'revenue_billion:-12.34567', context: '企业甲 · 2023 年 · 全年营收', sql: 'SELECT revenue_billion FROM company_finance', url: '', date: '' },
    { label: 'E9', title: '未引用资料', text: '不应出现', url: '', date: '' },
  ]
  const markdown = '资料 [E1]，数据 **[D1]**，未知 [E8]。`[E1]`。\n\n[E1](https://example.org/existing)\n\n| 企业 | 数值 |\n|---|---:|\n| 企业甲 | -12.34567[D1] |'
  const html = renderToStaticMarkup(createElement(EvidenceMarkdown, { references }, markdown))
  assert.equal((html.match(/href="#reference-E1"/g) ?? []).length, 2, '正文及已有链接引用可跳转')
  assert.equal((html.match(/href="#reference-D1"/g) ?? []).length, 2, '加粗和表格中的数据引用可跳转')
  assert.ok(html.includes('<code>[E1]</code>'), '代码中的引用保留原样')
  assert.ok(!html.includes('href="https://example.org/existing"'), '已知编号链接统一跳转到文末来源')
  assert.ok(!html.includes('href="#reference-E8"'), '未知引用不伪造来源')
  assert.ok(html.includes('<table>'), 'Markdown 表格显示为数据表')
  const forms = ['[E1](https://example.org/existing)', '[[E1]](https://example.org/existing)', '[来源](#evidence-E1)', '[来源](#reference-E1)', '[D1](https://example.org/data)', '[E1][source]\n\n[source]: https://example.org/source']
  for (const content of forms) {
    const output = renderToStaticMarkup(createElement(ReportView, { report: { sections: [{ title: '结论', content, sources: [] }], summary: '', charts_data: [], references } }))
    const label = content.startsWith('[D1]') ? 'D1' : 'E1'
    assert.ok(output.includes(`href="#reference-${label}"`) && output.includes(`id="reference-${label}"`), `${content} 引用有文末目标`)
    assert.ok(output.includes(`aria-label="查看来源 ${label}"`), '链接使用可交互的来源组件')
  }
  const ordinary = renderToStaticMarkup(createElement(EvidenceMarkdown, { references }, '[普通网站](https://example.org/ordinary) [E8](https://example.org/unknown)'))
  assert.ok(ordinary.includes('href="https://example.org/ordinary"') && ordinary.includes('href="https://example.org/unknown"'), '普通链接和未知编号链接保持目标')
  const codeOnly = renderToStaticMarkup(createElement(ReportView, { report: { sections: [{ title: '示例', content: '`[E9]`\n\n```text\n[E1]\n```', sources: [] }], summary: '', charts_data: [], references } }))
  assert.ok(!codeOnly.includes('参考来源') && !codeOnly.includes('id="reference-'), '仅代码中出现的编号不生成文末来源')
  for (const type of ['bar', 'line', 'pie']) {
    const chart = { title: '示例指标', type, data: [{ label: '企业甲', value: -12.34567 }, { label: '企业乙', value: 0 }, { label: '企业丙', value: 20.98765 }], image_b64: '', query: '', scope: '2023 年 · 教学样例', x_label: '企业', y_label: '原始指标' }
    const result = renderToStaticMarkup(createElement(ChartView, { chart }))
    assert.ok(result.includes('-12.34567') && result.includes('20.98765'), `${type} 保留原始数值`)
    assert.ok(!result.includes('NaN') && !result.includes('Infinity'), `${type} 正确处理零和负值`)
    assert.ok(result.includes('2023 年 · 教学样例'), `${type} 展示口径`)
  }
  const report = renderToStaticMarkup(createElement(ReportView, { report: { sections: [{ title: '结论', content: markdown, sources: [] }], summary: '', charts_data: [], references } }))
  assert.ok(report.includes('id="reference-E1"') && report.includes('id="reference-D1"'), '引用有对应文末锚点')
  assert.ok(!report.includes('未引用资料'), '文末仅展示引用到的来源')
  assert.ok(report.includes('&lt;script&gt;不能执行&lt;/script&gt;'), '来源原文安全显示')
  assert.ok(!report.includes('质量评分') && !report.includes('缓存命中'), '报告不展示内部指标')
  const longText = '完整来源原文。'.repeat(40)
  const longReport = renderToStaticMarkup(createElement(ReportView, { report: { sections: [{ title: '结论', content: '[E1]', sources: [] }], summary: '', charts_data: [], references: [{ ...references[0], text: longText }] } }))
  assert.ok(longReport.includes('查看完整引用内容') && longReport.includes(longText), '长来源保留完整原文并提供展开入口')
  assert.ok(!/<details[^>]*\bopen\b/.test(longReport), '长来源默认收起，不占满报告')
  console.log('Reader rendering verified: citations, tables, safe exact evidence, scoped charts, zero/negative values, and consumer-only report.')
} finally {
  await server.close()
}
