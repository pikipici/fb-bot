import { useQuery } from '@tanstack/react-query'
import { Gauge, Loader2 } from 'lucide-react'

import { api } from '@/services/api'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface QuotaStatus {
  allowed: boolean
  used: number
  remaining: number
  limit: number
  window_hours: number
  resets_at: string | null
}

interface RateLimitResponse {
  quota: QuotaStatus
}

const REFETCH_MS = 30_000

function formatRelative(iso: string | null): string {
  if (!iso) return '-'
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return iso
  const diff = ts - Date.now()
  if (diff <= 0) return 'sekarang'
  const mins = Math.ceil(diff / 60_000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  const rem = mins % 60
  return rem > 0 ? `${hours}j ${rem}m` : `${hours}j`
}

/**
 * Compact quota readout shown in the app header.
 *
 * Always visible on every page so the admin can see at a glance how
 * many of the 5/6h sends are left. Hits the same
 * /api/v1/rate-limit/status endpoint the Trending page uses, so
 * TanStack Query dedupes and shares cache entries.
 *
 * Hover (native title attribute) reveals window + reset breakdown
 * without needing a Radix Tooltip dep.
 */
export function QuotaWidget() {
  const { data, isLoading, isError } = useQuery<RateLimitResponse>({
    queryKey: ['rate-limit-status'],
    queryFn: () => api.getRateLimitStatus(),
    refetchInterval: REFETCH_MS,
  })

  if (isLoading || !data) {
    return (
      <div
        className="text-muted-foreground hidden items-center gap-1 rounded-md border px-2 py-1 text-xs md:flex"
        title="Mengambil quota…"
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        <span className="opacity-70">quota…</span>
      </div>
    )
  }

  if (isError) {
    return (
      <div
        className="text-muted-foreground hidden items-center gap-1 rounded-md border px-2 py-1 text-xs md:flex"
        title="Quota unavailable — refresh page"
      >
        <Gauge className="h-3 w-3" />
        <span className="opacity-70">quota ?</span>
      </div>
    )
  }

  const q = data.quota
  const low = q.remaining <= 1 && q.limit > 0
  const exhausted = !q.allowed

  const title = [
    `Quota komen ${q.used}/${q.limit} dalam ${q.window_hours}j`,
    `Sisa: ${q.remaining} komen`,
    q.resets_at
      ? `Reset: ${formatRelative(q.resets_at)} lagi`
      : 'Belum ada kirim di window ini',
  ].join('\n')

  return (
    <div
      className={cn(
        'hidden items-center gap-1.5 rounded-md border px-2 py-1 text-xs md:flex',
        exhausted &&
          'border-destructive/50 bg-destructive/10 text-destructive',
        !exhausted && low && 'border-amber-500/40 text-amber-500',
      )}
      title={title}
    >
      <Gauge className="h-3 w-3" />
      <span className="font-mono">
        {q.used}
        <span className="opacity-60">/{q.limit}</span>
      </span>
      {exhausted ? (
        <Badge
          variant="destructive"
          className="h-4 px-1 text-[9px] uppercase"
        >
          habis
        </Badge>
      ) : null}
    </div>
  )
}
