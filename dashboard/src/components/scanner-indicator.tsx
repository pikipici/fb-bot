import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, PlayCircle, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'

import { api } from '@/services/api'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/store/authStore'
import { cn } from '@/lib/utils'

/**
 * ScannerIndicator — surfaces the last scan_all_sources run so the Trending
 * header doesn't lie about data freshness.
 *
 * Before this existed, the header showed ``"update: baru saja"`` based on
 * React Query's ``dataUpdatedAt`` — but that's just when the dashboard
 * last fetched the trending list, NOT when the scanner actually pulled
 * fresh posts from FB. Users would hit Refresh and wonder why no new
 * posts appeared.
 *
 * Now we read ``GET /scanner/status`` every 10s and render:
 *   - ``scan terakhir: 3m lalu · 4 post baru`` (when last run succeeded)
 *   - ``scan gagal: cookie_expired`` (when last run aborted)
 *   - ``scanning…`` (with spinner while a run is in progress)
 *
 * Admins also get a ``Scan sekarang`` button that POSTs to /scanner/run-now
 * and flips the indicator into the scanning state. Viewers just see the
 * status.
 */

interface ScannerRun {
  id: number
  task_id: string | null
  trigger: 'beat' | 'manual' | string
  status: 'running' | 'success' | 'failed' | string
  started_at: string | null
  finished_at: string | null
  enabled_sources: number
  successful_scans: number
  scan_errors: number
  inserted: number
  updated: number
  skipped: number
  aborted_reason: string | null
  error_message: string | null
}

interface ScannerStatusResponse {
  is_running: boolean
  last_run: ScannerRun | null
  last_success: ScannerRun | null
}

function formatRelative(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diffMs = Date.now() - then
  const mins = Math.max(Math.round(diffMs / 60_000), 0)
  if (mins < 1) return 'baru saja'
  if (mins < 60) return `${mins}m lalu`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}j lalu`
  return `${Math.round(hours / 24)}h lalu`
}

export function ScannerIndicator() {
  const queryClient = useQueryClient()
  const { role, username } = useAuthStore()
  const isAdmin = role === 'admin'

  const statusQuery = useQuery<ScannerStatusResponse>({
    queryKey: ['scannerStatus'],
    queryFn: () => api.getScannerStatus(),
    enabled: Boolean(username),
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
  })

  const runNow = useMutation({
    mutationFn: () => api.runScanNow(),
    onSuccess: () => {
      toast.success('Scan dijalankan — post baru bakal muncul dalam ~1 menit')
      queryClient.invalidateQueries({ queryKey: ['scannerStatus'] })
    },
    onError: (err: any) => {
      toast.error(err?.message || 'Gagal trigger scan')
    },
  })

  // When the scanner flips from running → success and inserted new posts,
  // auto-invalidate the trending list so users see the new rows without
  // clicking Refresh.
  const lastRun = statusQuery.data?.last_run
  const lastRunKey = lastRun
    ? `${lastRun.id}-${lastRun.status}-${lastRun.inserted}`
    : null
  useEffect(() => {
    if (!lastRun) return
    if (lastRun.status === 'success' && lastRun.inserted > 0) {
      queryClient.invalidateQueries({ queryKey: ['trending'] })
    }
  }, [lastRunKey, queryClient, lastRun])

  const status = statusQuery.data

  let label = '—'
  let tone: 'muted' | 'danger' | 'active' = 'muted'

  if (status?.is_running) {
    label = 'scanning…'
    tone = 'active'
  } else if (status?.last_run) {
    const run = status.last_run
    if (run.status === 'success') {
      const when = formatRelative(run.finished_at || run.started_at)
      const inserted = run.inserted
      label = inserted > 0
        ? `scan terakhir: ${when} · ${inserted} post baru`
        : `scan terakhir: ${when} · tidak ada post baru`
      tone = 'muted'
    } else if (run.status === 'failed') {
      const reason = run.aborted_reason || 'error'
      label = `scan gagal: ${reason}`
      tone = 'danger'
    } else {
      label = `scan: ${run.status}`
      tone = 'muted'
    }
  } else {
    label = 'scanner belum jalan'
    tone = 'muted'
  }

  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          'hidden text-xs sm:inline',
          tone === 'muted' && 'text-muted-foreground',
          tone === 'active' && 'text-primary',
          tone === 'danger' && 'text-destructive',
        )}
      >
        {status?.is_running && (
          <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
        )}
        {label}
      </span>
      {isAdmin && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => runNow.mutate()}
          disabled={runNow.isPending || status?.is_running}
        >
          {runNow.isPending || status?.is_running ? (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
          )}
          Scan sekarang
        </Button>
      )}
    </div>
  )
}
