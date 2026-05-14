import { useQuery } from '@tanstack/react-query'
import { MessageSquare, Loader2 } from 'lucide-react'

import { api } from '@/services/api'

interface CommentActivityResponse {
  count_today: number
  date: string
  tz: string
}

const REFETCH_MS = 30_000

/**
 * Compact "Komen hari ini: N" readout shown in the app header.
 *
 * Replaces {@link QuotaWidget}: we stopped gating sends on a rolling
 * 5/6h window (preflight is effectively bypassed server-side via
 * ``MAX_COMMENTS_PER_WINDOW=9999``). This widget is informational only
 * — it never blocks, it just counts SENT ``CommentHistory`` rows inside
 * today's WIB calendar day.
 *
 * Hit ``/api/v1/comment-activity/today``; TanStack Query dedupes the
 * cache entry with the Trending page via the ``comment-activity-today``
 * key so ``sendComment`` invalidation keeps both in sync.
 */
export function CommentActivityWidget() {
  const { data, isLoading, isError } = useQuery<CommentActivityResponse>({
    queryKey: ['comment-activity-today'],
    queryFn: () => api.getCommentActivity(),
    refetchInterval: REFETCH_MS,
  })

  if (isLoading || !data) {
    return (
      <div
        className="text-muted-foreground hidden items-center gap-1 rounded-md border px-2 py-1 text-xs md:flex"
        title="Mengambil counter komen…"
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        <span className="opacity-70">komen hari ini…</span>
      </div>
    )
  }

  if (isError) {
    return (
      <div
        className="text-muted-foreground hidden items-center gap-1 rounded-md border px-2 py-1 text-xs md:flex"
        title="Counter komen gak bisa diambil — coba refresh"
      >
        <MessageSquare className="h-3 w-3" />
        <span className="opacity-70">komen hari ini ?</span>
      </div>
    )
  }

  return (
    <div
      className="text-muted-foreground hidden items-center gap-1.5 rounded-md border px-2 py-1 text-xs md:flex"
      title={`Reset tiap 00:00 WIB · tanggal ${data.date}`}
    >
      <MessageSquare className="h-3 w-3" />
      <span>
        Komen hari ini:{' '}
        <span className="text-foreground font-mono font-medium">
          {data.count_today}
        </span>
      </span>
    </div>
  )
}
