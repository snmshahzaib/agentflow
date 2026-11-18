/**
 * Renders report Markdown.
 *
 * The body is model-generated text that quotes retrieved web pages, so it is
 * treated as hostile input, not as content we authored. Three things make
 * that safe:
 *
 * 1. **No raw HTML.** `react-markdown` does not render embedded HTML unless
 *    `rehype-raw` is added, and it deliberately is not. A `<script>` or an
 *    `<img onerror=…>` in the Markdown is displayed as text.
 * 2. **An explicit URL allowlist.** Anything that is not http, https or
 *    mailto is dropped rather than linked, so `javascript:` and `data:`
 *    hrefs cannot become clickable. This is also react-markdown's default;
 *    it is restated here because relying on a library default for a security
 *    property makes the property invisible to the next reader.
 * 3. **No window handle for opened links.** Every link is external, so each
 *    one carries `rel="noopener noreferrer"`.
 *
 * Covered by component tests and a browser security spec that feeds a report
 * containing a script tag, an onerror handler and a `javascript:` link.
 */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { safeUrl } from '@/lib/urls'

/**
 * Heading levels are shifted down by `headingOffset`.
 *
 * A report is a document rendered *inside* a page that already has an `h1`
 * and `h2`s of its own. Emitting the report's own `#` as an `h1` produced
 * nine `h2`s inside one panel and an invalid outline, which is a real
 * navigation problem for anyone moving by heading rather than a cosmetic
 * one. With the default offset the report's top heading becomes an `h3`,
 * nested under the task it belongs to.
 */
function shiftHeading(level: number, offset: number) {
  const target = Math.min(level + offset, 6)
  return `h${target}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'
}

export function Markdown({
  children,
  className = '',
  headingOffset = 2,
}: {
  children: string
  className?: string
  headingOffset?: number
}) {
  return (
    <div className={`md ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
        components={{
          h1: ({ children: inner }) => {
            const Tag = shiftHeading(1, headingOffset)
            return <Tag>{inner}</Tag>
          },
          h2: ({ children: inner }) => {
            const Tag = shiftHeading(2, headingOffset)
            return <Tag>{inner}</Tag>
          },
          h3: ({ children: inner }) => {
            const Tag = shiftHeading(3, headingOffset)
            return <Tag>{inner}</Tag>
          },
          h4: ({ children: inner }) => {
            const Tag = shiftHeading(4, headingOffset)
            return <Tag>{inner}</Tag>
          },
          a({ href, children: text, ...rest }) {
            const target = href ? safeUrl(href) : ''
            if (!target) {
              // Keep the text, drop the link. Silently rendering nothing
              // would hide that the report said something here.
              return <span>{text}</span>
            }
            return (
              <a {...rest} href={target} target="_blank" rel="noopener noreferrer">
                {text}
              </a>
            )
          },
          table({ children: rows, ...rest }) {
            // The claim-support table is the densest thing in a report and
            // the first to overflow a phone. It scrolls in its own box so the
            // page itself never scrolls sideways.
            return (
              <div className="md-table-scroll" role="group" tabIndex={0} aria-label="Table">
                <table {...rest}>{rows}</table>
              </div>
            )
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
