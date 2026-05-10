import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
  MutationCache,
} from '@tanstack/react-query'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'

import { useAuthStore } from './store/authStore'
import { ThemeProvider } from '@/components/theme-provider'
import { Toaster } from '@/components/ui/sonner'
import { ErrorBoundary } from '@/components/error-boundary'

// Login is the only route rendered pre-auth — keep it eager so the
// initial bundle can boot to it instantly. Everything else lazy-loads
// on first navigation so the initial JS payload stays small.
import Login from './pages/Login'

const Trending = lazy(() => import('./pages/Trending'))
const History = lazy(() => import('./pages/History'))
const FBAccounts = lazy(() => import('./pages/FBAccounts'))
const Sources = lazy(() => import('./pages/Sources'))
const Template = lazy(() => import('./pages/Template'))

// Global React Query error surfacing. Page-level mutations can still
// attach their own onError toast — this only fires for errors that
// reach the bubble (i.e. not caught by the caller).
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      // Ignore "Unauthorized" — api.ts already redirects to /login.
      if ((error as Error)?.message === 'Unauthorized') return
      // Don't double-toast silent background refetches the user didn't
      // trigger — only if they're actively looking at the query.
      if (query.state.data !== undefined) return
      const msg = (error as Error)?.message || 'Gagal fetch data'
      toast.error(`Fetch error: ${msg}`)
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if ((error as Error)?.message === 'Unauthorized') return
      // Only toast mutations that don't set their own onError.
      if (mutation.options.onError) return
      const msg = (error as Error)?.message || 'Action gagal'
      toast.error(msg)
    },
  }),
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.accessToken)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.accessToken)
  const role = useAuthStore((s) => s.role)
  if (!token) return <Navigate to="/login" replace />
  if (role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

function PageLoader() {
  return (
    <div className="bg-background flex min-h-screen items-center justify-center">
      <Loader2 className="text-muted-foreground h-6 w-6 animate-spin" />
    </div>
  )
}

function App() {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="fb-bot-ui-theme">
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary scope="aplikasi">
          <BrowserRouter>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path="/login" element={<Login />} />
                <Route
                  path="/"
                  element={
                    <ProtectedRoute>
                      <ErrorBoundary scope="halaman Trending">
                        <Trending />
                      </ErrorBoundary>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/history"
                  element={
                    <ProtectedRoute>
                      <ErrorBoundary scope="halaman History">
                        <History />
                      </ErrorBoundary>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/accounts"
                  element={
                    <AdminRoute>
                      <ErrorBoundary scope="halaman Accounts">
                        <FBAccounts />
                      </ErrorBoundary>
                    </AdminRoute>
                  }
                />
                <Route
                  path="/sources"
                  element={
                    <AdminRoute>
                      <ErrorBoundary scope="halaman Sumber">
                        <Sources />
                      </ErrorBoundary>
                    </AdminRoute>
                  }
                />
                <Route
                  path="/template"
                  element={
                    <AdminRoute>
                      <ErrorBoundary scope="halaman Template">
                        <Template />
                      </ErrorBoundary>
                    </AdminRoute>
                  }
                />
              </Routes>
            </Suspense>
          </BrowserRouter>
        </ErrorBoundary>
        <Toaster richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
