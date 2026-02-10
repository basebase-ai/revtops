import { QueryClient } from '@tanstack/react-query';

// Configure React Query with sensible defaults
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Refetch on window focus to keep data fresh
      refetchOnWindowFocus: true,
      // Cache data for 5 minutes
      staleTime: 5 * 60 * 1000,
      // Keep unused data in cache for 10 minutes
      gcTime: 10 * 60 * 1000,
      // Retry failed requests 2 times
      retry: 2,
    },
  },
});
