/**
 * Client-side URL safety.
 *
 * The backend already rejects unsafe source URLs before storing them
 * (`app/adapters/search/urls.py`), so this is defence in depth rather than
 * the only check. It exists because the two failures it prevents are cheap to
 * prevent and expensive to discover: a `javascript:` href rendered as a link,
 * and a `data:` URL used to smuggle a document into a new tab.
 */

const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:']

/** The URL if it is safe to link to, otherwise an empty string. */
export function safeUrl(url: string): string {
  let parsed: URL
  try {
    // Relative URLs carry no protocol and are safe by construction; the base
    // only makes them parseable.
    parsed = new URL(url, 'https://agentflow.invalid')
  } catch {
    return ''
  }
  return SAFE_PROTOCOLS.includes(parsed.protocol) ? url : ''
}
