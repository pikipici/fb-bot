import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { useAuthStore } from './store/authStore'
import Login from './pages/Login'
import ReviewQueue from './pages/ReviewQueue'
import FBAccounts from './pages/FBAccounts'
import Sources from './pages/Sources'
import { ThemeProvider } from '@/components/theme-provider'
import { Toaster } from '@/components/ui/sonner'

const queryClient = new QueryClient()

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
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <ReviewQueue />
                </ProtectedRoute>
              }
            />
            <Route
              path="/accounts"
              element={
                <AdminRoute>
                  <FBAccounts />
                </AdminRoute>
              }
            />
            <Route
              path="/sources"
              element={
                <AdminRoute>
                  <Sources />
                </AdminRoute>
              }
            />
          </Routes>
        </BrowserRouter>
        <Toaster richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
