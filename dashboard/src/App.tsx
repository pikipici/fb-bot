import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from './store/authStore'
import Login from './pages/Login'
import ReviewQueue from './pages/ReviewQueue'
// DISABLED: multi-account rotation — using single account from .env
// import FBAccounts from './pages/FBAccounts'

const queryClient = new QueryClient()

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.accessToken)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function App() {
  return (
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
          {/* DISABLED: multi-account rotation — using single account from .env */}
          {/* <Route
            path="/accounts"
            element={
              <ProtectedRoute>
                <FBAccounts />
              </ProtectedRoute>
            }
          /> */}
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
