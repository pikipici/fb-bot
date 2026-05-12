import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ShieldAlert } from 'lucide-react'

import { api } from '@/services/api'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/store/authStore'

/**
 * Global banner that surfaces FB account cookie-session health.
 *
 * Shows a red bar at the top of every page when the connected FB
 * account is in ``EXPIRED`` or ``BLOCKED`` state so users don't burn
 * a Send attempt just to learn their cookie is dead.
 *
 * The FBAccounts page already shows a per-card warning — this banner
 * is the cross-page nudge. Hidden when account is ACTIVE / COOLDOWN
 * or when no account is connected yet.
 *
 * Polls every 60s because status flips happen in background (scanner
 * beat, send attempt). Re-uses the ``fbAccountCurrent`` query key so
 * any mutation-triggered invalidation hits this banner too.
 */
export function AccountStatusBanner() {
  const { role, username } = useAuthStore()
  const navigate = useNavigate()

  const { data } = useQuery({
    queryKey: ['fbAccountCurrent'],
    queryFn: () => api.getCurrentFBAccount(),
    // only poll when authenticated — otherwise the request 401s repeatedly
    enabled: Boolean(username),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })

  const account = data?.account ?? null
  if (!account) return null

  const status = account.status as string
  if (status !== 'EXPIRED' && status !== 'BLOCKED' && status !== 'CHECKPOINT') return null

  const message =
    status === 'EXPIRED'
      ? 'Cookie FB lu expired — scanner & send gak bakal jalan sampai di-reconnect.'
      : status === 'CHECKPOINT'
        ? 'FB minta checkpoint/verifikasi. Selesain di browser dulu, lalu re-upload cookie yang baru.'
        : 'Akun FB terblokir oleh Facebook — scanner & send ditahan.'

  const canManage = role === 'admin'

  return (
    <div className="bg-destructive/10 text-destructive border-destructive/30 border-b">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2 text-sm sm:px-6">
        <ShieldAlert className="size-4 shrink-0" />
        <span className="flex-1">{message}</span>
        {canManage && (
          <Button
            size="sm"
            variant="outline"
            className="border-destructive/40 text-destructive hover:bg-destructive/20"
            onClick={() => navigate('/accounts')}
          >
            Buka Accounts
          </Button>
        )}
      </div>
    </div>
  )
}
