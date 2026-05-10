import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Loader2,
  RefreshCw,
} from 'lucide-react'

import { api } from '../services/api'
import { AppHeader } from '@/components/app-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardHeader,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

type StatusFilter = 'ALL' | 'SENT' | 'FAILED' | 'PENDING'

interface HistoryPostSummary {
  id: number
  fb_post_id: string
  author_name: string | null
  text_snippet: string | null
  post_url: string | null
  thumbnail_url: string | null
  status: string
}

interface HistoryItem {
  id: number
  trending_post_id: number
  user_id: number | null
  comment_text: string
  fb_comment_id: string | null
  status: string
  error_message: string | null
  sent_at: string | null
  post: HistoryPostSummary | null
}

interface HistoryResponse {
  items: HistoryItem[]
  total: number
}

const PAGE_SIZE = 25
const REFETCH_MS = 30_000

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    return d.toLocaleString('id-ID', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatRelative(iso: string | null): string {
  if (!iso) return '-'
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return iso
  const diff = Date.now() - ts
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'baru aja'
  if (mins < 60) return `${mins}m lalu`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}j lalu`
  const days = Math.floor(hours / 24)
  return `${days}h lalu`
}

function statusBadge(status: string) {
  switch (status) {
    case 'SENT':
      return (
        <Badge className="bg-emerald-600 hover:bg-emerald-600/90">
          <CheckCircle2 className="mr-1 h-3 w-3" />
          Sent
        </Badge>
      )
    case 'FAILED':
      return (
        <Badge variant="destructive">
          <AlertCircle className="mr-1 h-3 w-3" />
          Failed
        </Badge>
      )
    case 'PENDING':
      return (
        <Badge variant="secondary">
          <Clock className="mr-1 h-3 w-3" />
          Pending
        </Badge>
      )
    default:
      return <Badge variant="outline">{status}</Badge>
  }
}

function HistoryRow({ item }: { item: HistoryItem }) {
  const post = item.post
  const title =
    post?.text_snippet?.trim() ||
    (post ? `Post #${post.fb_post_id}` : 'Post dihapus')
  const titleTrunc =
    title.length > 180 ? title.slice(0, 180) + '…' : title

  return (
    <Card className="overflow-hidden">
      <CardHeader className="gap-1 pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              {statusBadge(item.status)}
              <span className="text-muted-foreground text-xs">
                {formatRelative(item.sent_at)}
              </span>
              <span className="text-muted-foreground text-[10px] opacity-60">
                {formatDate(item.sent_at)}
              </span>
            </div>
            <div className="text-muted-foreground mt-1 truncate text-xs">
              Post by{' '}
              <span className="text-foreground font-medium">
                {post?.author_name || 'Unknown'}
              </span>
            </div>
          </div>
          {post?.post_url ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                window.open(
                  post.post_url!,
                  '_blank',
                  'noopener,noreferrer',
                )
              }
              title="Buka post di FB"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-2 pb-3">
        <p className="text-muted-foreground text-xs italic whitespace-pre-wrap break-words">
          {titleTrunc}
        </p>

        <div className="rounded-md border bg-muted/30 p-2 text-sm whitespace-pre-wrap break-words">
          {item.comment_text}
        </div>

        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]">
          {item.fb_comment_id ? (
            <span>
              FB id:{' '}
              <span className="text-foreground font-mono">
                {item.fb_comment_id}
              </span>
            </span>
          ) : item.status === 'SENT' ? (
            <span className="opacity-70">FB id tidak tersedia</span>
          ) : null}
          {item.error_message ? (
            <span className="text-destructive truncate">
              err: {item.error_message}
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

export default function History() {
  const [status, setStatus] = useState<StatusFilter>('ALL')
  const [page, setPage] = useState(0)

  const offset = page * PAGE_SIZE

  const { data, isLoading, isFetching, refetch, isError, error } = useQuery<
    HistoryResponse
  >({
    queryKey: ['history', status, offset],
    queryFn: () =>
      api.listHistory({
        status: status === 'ALL' ? undefined : status,
        limit: PAGE_SIZE,
        offset,
      }),
    refetchInterval: REFETCH_MS,
  })

  const total = data?.total ?? 0
  const items = data?.items ?? []
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const hasPrev = page > 0
  const hasNext = page + 1 < totalPages

  return (
    <div className="bg-background min-h-screen">
      <AppHeader />

      <main className="mx-auto max-w-6xl space-y-4 px-4 py-6 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Comment History
            </h1>
            <p className="text-muted-foreground text-sm">
              Log semua komen yang udah dikirim bot — audit trail Layer 2.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={status}
              onValueChange={(v) => {
                setStatus(v as StatusFilter)
                setPage(0)
              }}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">Semua</SelectItem>
                <SelectItem value="SENT">Sent</SelectItem>
                <SelectItem value="FAILED">Failed</SelectItem>
                <SelectItem value="PENDING">Pending</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              {isFetching ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              )}
              Refresh
            </Button>
          </div>
        </div>

        {isError ? (
          <Card className="border-destructive/50">
            <CardContent className="py-6 text-sm">
              <div className="text-destructive font-medium">
                Gagal load history
              </div>
              <div className="text-muted-foreground mt-1 text-xs">
                {(error as Error | undefined)?.message ||
                  'Unknown error — coba refresh'}
              </div>
            </CardContent>
          </Card>
        ) : isLoading ? (
          <Card>
            <CardContent className="py-12 text-center">
              <Loader2 className="text-muted-foreground mx-auto h-6 w-6 animate-spin" />
            </CardContent>
          </Card>
        ) : items.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <div className="text-muted-foreground text-sm">
                Belum ada komen yang dikirim
                {status !== 'ALL' ? ` dengan status ${status}` : ''}.
              </div>
              <div className="text-muted-foreground mt-1 text-xs opacity-70">
                Kirim komen pertama lu dari halaman Trending.
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {items.map((item) => (
              <HistoryRow key={item.id} item={item} />
            ))}
          </div>
        )}

        {total > 0 && (
          <div className="flex items-center justify-between border-t pt-3">
            <div className="text-muted-foreground text-xs">
              {total} total · halaman {page + 1} / {totalPages}
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={!hasPrev}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => p + 1)}
                disabled={!hasNext}
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
