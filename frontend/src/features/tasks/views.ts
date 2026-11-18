/** Which section of a task's detail is open. Mirrored into the URL. */
export type View = 'report' | 'sources' | 'claims' | 'workflow'

export const VIEWS: readonly View[] = ['report', 'sources', 'claims', 'workflow']

/** Narrows a raw query-string value, so a hand-edited URL cannot break the UI. */
export function isView(value: string | null): value is View {
  return value !== null && (VIEWS as readonly string[]).includes(value)
}
