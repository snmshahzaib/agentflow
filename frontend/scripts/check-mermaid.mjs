/**
 * Parse every Mermaid block in the documentation.
 *
 * A broken diagram does not fail a build anywhere -- it renders as an error
 * box on GitHub, which is a bad way to find out. This runs Mermaid's own
 * parser over each block so a syntax error is a failing check instead.
 */

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'

import { JSDOM } from 'jsdom'

// Mermaid's parser touches the DOM even when only validating.
const dom = new JSDOM('<!doctype html><html><body></body></html>')
globalThis.window = dom.window
globalThis.document = dom.window.document
// navigator is a getter-only global on modern Node, so it is defined rather
// than assigned.
Object.defineProperty(globalThis, 'navigator', {
  value: dom.window.navigator,
  configurable: true,
})

const { default: mermaid } = await import('mermaid')
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })

const ROOT = path.resolve(process.argv[2] ?? '.')
const BLOCK = /```mermaid\n([\s\S]*?)```/g

async function* markdownFiles(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name === 'node_modules') continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) yield* markdownFiles(full)
    else if (entry.name.endsWith('.md')) yield full
  }
}

let checked = 0
const failures = []

for await (const file of markdownFiles(ROOT)) {
  const text = await readFile(file, 'utf8')
  let match
  let index = 0
  while ((match = BLOCK.exec(text)) !== null) {
    index += 1
    checked += 1
    const diagram = match[1]
    const line = text.slice(0, match.index).split('\n').length
    try {
      await mermaid.parse(diagram)
    } catch (error) {
      failures.push(`${path.relative(ROOT, file)}:${line} (block ${index}) — ${error.message}`)
    }
  }
}

if (failures.length > 0) {
  console.error(`\n${failures.length} of ${checked} Mermaid diagrams failed to parse:\n`)
  for (const failure of failures) console.error(`  ${failure}`)
  process.exit(1)
}

console.log(`  ${checked} Mermaid diagrams parse cleanly`)
