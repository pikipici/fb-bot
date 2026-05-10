import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
  MutationCache,
} from '@tanstack/react-query'
import { toast } from 'sonner'

import { useAuthStore } from './store/authStore'
import Login from './pages/Login'
import ReviewQueue from './pages/ReviewQueue'
import FBAccounts from './pages/FBAccounts'
import Sources from './pages/Sources'
import Trending from './pages/Trending'
import Template from './pages/Template'
import History from './pages/History'
import { ThemeProvider } from '@/components/theme-provider'
import { Toaster } from '@/components/ui/sonner'
import { ErrorBoundary } from '@/components/error-boundary'

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

function App() {
  return (
    <ThemeProvider defaultTheme="system" storageKey="fb-bot-ui-theme">
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary scope="aplikasi">
          <BrowserRouter>
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
                path="/review"
                element={
                  <ProtectedRoute>
                    <ErrorBoundary scope="halaman Review">
                      <ReviewQueue />
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
          </BrowserRouter>
        </ErrorBoundary>
        <Toaster richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
