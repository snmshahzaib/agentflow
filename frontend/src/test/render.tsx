import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'

/** Renders with a fresh QueryClient and retries disabled, so a failing
 *  request surfaces immediately instead of after backoff. */
export function renderWithQuery(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return {
    queryClient,
    ...render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>),
  }
}
