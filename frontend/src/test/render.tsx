import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'

/** Renders with a fresh QueryClient and retries disabled, so a failing
 *  request surfaces immediately instead of after backoff. */
export function renderWithQuery(ui: ReactElement) {
  const queryClient = newClient()
  return {
    queryClient,
    ...render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>),
  }
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
}

/** Wrapper for `renderHook`, which takes a component rather than an element. */
export function withQuery() {
  const queryClient = newClient()
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}
