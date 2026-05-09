import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '../store/authStore'
import { useNavigate } from 'react-router-dom'
import { api } from '../services/api'

interface FBAccount {
  id: number
  label: string
  email: string
  status: string
  purpose: string
  last_used_at: string | null
  cooldown_until: string | null
  failure_count: number
  total_uses: number
  notes: string | null
  created_at: string
}

export default function FBAccounts() {
  const { username, role, logout } = useAuthStore()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [showForm, setShowForm] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState({ label: '', email: '', password: '', purpose: 'both', notes: '' })

  const { data, isLoading } = useQuery({
    queryKey: ['fbAccounts'],
    queryFn: () => api.getFBAccounts(true),
  })

  const createMutation = useMutation({
    mutationFn: (data: typeof form) => api.createFBAccount(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccounts'] })
      resetForm()
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<typeof form> }) => api.updateFBAccount(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccounts'] })
      resetForm()
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteFBAccount(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fbAccounts'] }),
  })

  const reactivateMutation = useMutation({
    mutationFn: (id: number) => api.reactivateFBAccount(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fbAccounts'] }),
  })

  function resetForm() {
    setForm({ label: '', email: '', password: '', purpose: 'both', notes: '' })
    setShowForm(false)
    setEditId(null)
  }

  function handleEdit(account: FBAccount) {
    setForm({
      label: account.label,
      email: account.email,
      password: '',
      purpose: account.purpose,
      notes: account.notes || '',
    })
    setEditId(account.id)
    setShowForm(true)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (editId) {
      const data: Record<string, string> = {}
      if (form.label) data.label = form.label
      if (form.email) data.email = form.email
      if (form.password) data.password = form.password
      if (form.purpose) data.purpose = form.purpose
      data.notes = form.notes
      updateMutation.mutate({ id: editId, data })
    } else {
      createMutation.mutate(form)
    }
  }

  function handleDelete(id: number, label: string) {
    if (confirm(`Delete account "${label}"? This cannot be undone.`)) {
      deleteMutation.mutate(id)
    }
  }

  const handleLogout = () => { logout(); navigate('/login') }

  const accounts: FBAccount[] = data?.accounts || []

  const statusBadge = (status: string) => {
    const colors: Record<string, string> = {
      ACTIVE: 'bg-green-900 text-green-300',
      COOLDOWN: 'bg-yellow-900 text-yellow-300',
      BLOCKED: 'bg-red-900 text-red-300',
      DISABLED: 'bg-gray-700 text-gray-400',
    }
    return colors[status] || 'bg-gray-700 text-gray-300'
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <header className="bg-gray-800 border-b border-gray-700 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-bold">FB Engagement Assistant</h1>
          <nav className="flex gap-2 text-sm">
            <button onClick={() => navigate('/')} className="text-gray-400 hover:text-white px-2 py-1">Review</button>
            <button className="text-white bg-gray-700 px-2 py-1 rounded">Accounts</button>
          </nav>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-400">
            {username} <span className="text-xs bg-gray-700 px-2 py-0.5 rounded">{role}</span>
          </span>
          <button onClick={handleLogout} className="text-sm text-red-400 hover:text-red-300">Logout</button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold">Facebook Accounts</h2>
          <button
            onClick={() => { resetForm(); setShowForm(true) }}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium"
          >
            + Add Account
          </button>
        </div>

        {/* Form */}
        {showForm && (
          <div className="bg-gray-800 rounded-lg p-6 mb-6 border border-gray-700">
            <h3 className="text-lg font-medium mb-4">{editId ? 'Edit Account' : 'Add New Account'}</h3>
            <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Label</label>
                <input
                  type="text"
                  value={form.label}
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500"
                  placeholder="e.g. Account 1"
                  required
                />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Email / Phone</label>
                <input
                  type="text"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500"
                  placeholder="email@example.com"
                  required={!editId}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">
                  Password {editId && <span className="text-gray-500">(leave empty to keep current)</span>}
                </label>
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500"
                  required={!editId}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Purpose</label>
                <select
                  value={form.purpose}
                  onChange={(e) => setForm({ ...form, purpose: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="both">Both (Scrape + Post)</option>
                  <option value="scrape">Scrape Only</option>
                  <option value="post">Post Only</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-sm text-gray-300 mb-1">Notes</label>
                <input
                  type="text"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500"
                  placeholder="Optional notes..."
                />
              </div>
              <div className="col-span-2 flex gap-3">
                <button
                  type="submit"
                  className="px-4 py-2 bg-green-600 hover:bg-green-700 rounded text-sm font-medium"
                >
                  {editId ? 'Save Changes' : 'Add Account'}
                </button>
                <button
                  type="button"
                  onClick={resetForm}
                  className="px-4 py-2 bg-gray-600 hover:bg-gray-500 rounded text-sm font-medium"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Account List */}
        {isLoading && <p className="text-gray-400">Loading accounts...</p>}

        {!isLoading && accounts.length === 0 && (
          <div className="bg-gray-800 rounded-lg p-8 text-center text-gray-400">
            No Facebook accounts added yet.
          </div>
        )}

        <div className="space-y-3">
          {accounts.map((account) => (
            <div key={account.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-1">
                    <span className="font-medium">{account.label}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${statusBadge(account.status)}`}>
                      {account.status}
                    </span>
                    <span className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">
                      {account.purpose}
                    </span>
                  </div>
                  <div className="text-sm text-gray-400 flex gap-4">
                    <span>{account.email}</span>
                    <span>Uses: {account.total_uses}</span>
                    <span>Failures: {account.failure_count}</span>
                    {account.last_used_at && (
                      <span>Last used: {new Date(account.last_used_at).toLocaleString()}</span>
                    )}
                  </div>
                  {account.notes && (
                    <p className="text-xs text-gray-500 mt-1">{account.notes}</p>
                  )}
                </div>

                <div className="flex gap-2 shrink-0">
                  {account.status === 'BLOCKED' && (
                    <button
                      onClick={() => reactivateMutation.mutate(account.id)}
                      className="px-3 py-1.5 bg-yellow-700 hover:bg-yellow-600 rounded text-sm"
                    >
                      Reactivate
                    </button>
                  )}
                  <button
                    onClick={() => handleEdit(account)}
                    className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600 rounded text-sm"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(account.id, account.label)}
                    className="px-3 py-1.5 bg-red-700 hover:bg-red-600 rounded text-sm"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
