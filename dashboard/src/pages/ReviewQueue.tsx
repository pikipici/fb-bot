import { useQuery } from '@tanstack/react-query'
import { useAuthStore } from '../store/authStore'
import { api } from '../services/api'
import { useNavigate } from 'react-router-dom'

interface Draft {
  id: number
  post_id: number
  text: string | null
  source_type: string
  status: string
  created_at: string
}

export default function ReviewQueue() {
  const { username, role, logout } = useAuthStore()
  const navigate = useNavigate()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['pendingDrafts'],
    queryFn: () => api.getPendingDrafts(),
  })

  const handleAction = async (draftId: number, action: string) => {
    try {
      await api.approveDraft(draftId, action)
      refetch()
    } catch (err: any) {
      alert(err.message || 'Action failed')
    }
  }

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const drafts: Draft[] = data?.drafts || []

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-bold">FB Engagement Assistant</h1>
          <nav className="flex gap-2 text-sm">
            <button className="text-white bg-gray-700 px-2 py-1 rounded">Review</button>
            {/* DISABLED: multi-account rotation — using single account from .env */}
            {/* <button onClick={() => navigate('/accounts')} className="text-gray-400 hover:text-white px-2 py-1">Accounts</button> */}
          </nav>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-400">
            {username} <span className="text-xs bg-gray-700 px-2 py-0.5 rounded">{role}</span>
          </span>
          <button
            onClick={handleLogout}
            className="text-sm text-red-400 hover:text-red-300"
          >
            Logout
          </button>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-4xl mx-auto p-6">
        <h2 className="text-xl font-semibold mb-4">Review Queue</h2>

        {isLoading && <p className="text-gray-400">Loading drafts...</p>}

        {!isLoading && drafts.length === 0 && (
          <div className="bg-gray-800 rounded-lg p-8 text-center text-gray-400">
            No pending drafts to review.
          </div>
        )}

        <div className="space-y-4">
          {drafts.map((draft) => (
            <div key={draft.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs bg-blue-900 text-blue-300 px-2 py-0.5 rounded">
                      {draft.source_type}
                    </span>
                    <span className="text-xs text-gray-500">
                      Post #{draft.post_id}
                    </span>
                  </div>
                  <p className="text-gray-200">
                    {draft.text || <span className="italic text-gray-500">No draft text (needs manual write)</span>}
                  </p>
                </div>

                {(role === 'operator' || role === 'admin') && draft.text && (
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => handleAction(draft.id, 'approve')}
                      className="px-3 py-1.5 bg-green-700 hover:bg-green-600 rounded text-sm font-medium transition-colors"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => handleAction(draft.id, 'reject')}
                      className="px-3 py-1.5 bg-red-700 hover:bg-red-600 rounded text-sm font-medium transition-colors"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
