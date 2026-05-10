import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ExternalLink,
  Flame,
  Gauge,
  Loader2,
  MessageCircle,
  RefreshCw,
  Repeat2,
  Send,
  Sparkles,
  ThumbsUp,
  TrendingUp,
  Wand2,
  X,
} from 'lucide-react'
import { toast } from 'sonner'

import { api } from '../services/api'
import { AppHeader } from '@/components/app-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore } from '@/store/authStore'
import { cn } from '@/lib/utils'

type SortKey = 'score' | 'velocity' | 'recent'
type StatusKey = 'ALL' | 'NEW' | 'DRAFTED' | 'SKIPPED' | 'COMMENTED'

interface TrendingSource {
  id: number
  type: string
  label: string
}

interface TrendingPost {
  id: number
  fb_post_id: string
  author_name: string | null
  text_snippet: string | null
  post_url: string | null
  unsupported_kind: string | null
  thumbnail_url: string | null
  likes: number
  comments: number
  shares: number
  reactions_total: number
  score: number
  velocity: number
  post_timestamp: string | null
  collected_at: string | null
  status: string
  source: TrendingSource | null
}

interface TrendingResponse {
  posts: TrendingPost[]
  total: number
}

interface SourceRow {
  id: number
  type: string
  label: string
}

interface DraftResponse {
  draft_text: string
  post: TrendingPost
}

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

interface SendCommentResponse {
  result: {
    success: boolean
    comment_text: string
    post_url: string
    fb_comment_id: string | null
    error: string | null
  }
  post: TrendingPost
  quota: QuotaStatus
}

const REFETCH_MS = 30_000

function formatCount(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'K'
  return String(n)
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
  const days = Math.round(hours / 24)
  return `${days}h lalu`
}

function statusBadge(status: string) {
  switch (status) {
    case 'NEW':
      return <Badge variant="default">Baru</Badge>
    case 'DRAFTED':
      return <Badge variant="secondary">Drafted</Badge>
    case 'SKIPPED':
      return <Badge variant="outline">Skipped</Badge>
    case 'COMMENTED':
      return <Badge className="bg-emerald-600 hover:bg-emerald-600/90">Commented</Badge>
    default:
      return <Badge variant="outline">{status}</Badge>
  }
}

function PostCard({
  post,
  isAdmin,
  activeDraft,
  onStartDraft,
  onCancelDraft,
  onDraftChange,
  onSkip,
  onSend,
  onAIDraft,
  isDrafting,
  isAIDrafting,
  isSkipping,
  isSending,
  sendDisabled,
  sendDisabledReason,
}: {
  post: TrendingPost
  isAdmin: boolean
  activeDraft: string | null
  onStartDraft: (postId: number) => void
  onCancelDraft: () => void
  onDraftChange: (text: string) => void
  onSkip: (postId: number) => void
  onSend: (postId: number, text: string) => void
  onAIDraft: (postId: number) => void
  isDrafting: boolean
  isAIDrafting: boolean
  isSkipping: boolean
  isSending: boolean
  sendDisabled: boolean
  sendDisabledReason: string
}) {
  const text = post.text_snippet?.trim() || ''
  const truncated = text.length > 280 ? text.slice(0, 280) + '…' : text
  const draftOpen = activeDraft !== null
  const unsupported = post.unsupported_kind !== null
  const canDraft = isAdmin && post.status !== 'COMMENTED' && !unsupported
  const canSkip = isAdmin && post.status !== 'COMMENTED'
  const draftTrimmed = (activeDraft || '').trim()

  const effectiveSendDisabled = sendDisabled || unsupported
  const effectiveSendDisabledReason = unsupported
    ? `Tipe ${post.unsupported_kind} gak bisa dikomen via bot`
    : sendDisabledReason

  return (
    <Card className="flex flex-col overflow-hidden">
      {post.thumbnail_url && (
        <div className="bg-muted aspect-video w-full overflow-hidden">
          <img
            src={post.thumbnail_url}
            alt=""
            className="h-full w-full object-cover"
            loading="lazy"
            onError={(e) => {
              ;(e.target as HTMLImageElement).style.display = 'none'
            }}
          />
        </div>
      )}
      <CardHeader className="gap-1 pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">
              {post.author_name || 'Unknown'}
            </div>
            <div className="text-muted-foreground flex flex-wrap items-center gap-1.5 text-xs">
              {post.source && (
                <>
                  <span className="truncate">{post.source.label}</span>
                  <span className="opacity-50">•</span>
                </>
              )}
              <span>{formatRelative(post.collected_at)}</span>
            </div>
          </div>
          {statusBadge(post.status)}
          {unsupported && (
            <Badge
              variant="outline"
              className="border-amber-500/50 bg-amber-500/10 text-amber-500"
              title={`Tipe ${post.unsupported_kind} gak bisa dikomen via bot — Skip aja`}
            >
              {post.unsupported_kind}
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="flex-1 space-y-3 pb-3">
        {truncated ? (
          <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
            {truncated}
          </p>
        ) : (
          <p className="text-muted-foreground text-sm italic">
            (tidak ada teks)
          </p>
        )}

        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="flex items-center gap-1">
            <ThumbsUp className="h-3.5 w-3.5" />
            {formatCount(post.likes)}
          </span>
          <span className="flex items-center gap-1">
            <MessageCircle className="h-3.5 w-3.5" />
            {formatCount(post.comments)}
          </span>
          <span className="flex items-center gap-1">
            <Repeat2 className="h-3.5 w-3.5" />
            {formatCount(post.shares)}
          </span>
          <span className="ml-auto flex items-center gap-1 font-medium text-orange-500">
            <Flame className="h-3.5 w-3.5" />
            {formatCount(Math.round(post.score))}
          </span>
        </div>

        {draftOpen && (
          <div className="space-y-2 rounded-md border p-2">
            <div className="text-muted-foreground flex items-center justify-between text-xs">
              <span className="font-medium">Draft komen</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={onCancelDraft}
              >
                <X className="mr-1 h-3 w-3" />
                Tutup
              </Button>
            </div>
            <Textarea
              value={activeDraft}
              onChange={(e) => onDraftChange(e.target.value)}
              className="min-h-[90px] font-mono text-sm"
              placeholder="Teks komen..."
              disabled={isSending}
            />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-[10px]">
                {effectiveSendDisabled
                  ? effectiveSendDisabledReason
                  : `${draftTrimmed.length} karakter`}
              </span>
              <Button
                size="sm"
                disabled={
                  isSending || effectiveSendDisabled || draftTrimmed.length === 0
                }
                onClick={() => onSend(post.id, draftTrimmed)}
                title={
                  effectiveSendDisabled
                    ? effectiveSendDisabledReason
                    : 'Kirim komen ke FB'
                }
              >
                {isSending ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Send className="mr-1.5 h-3.5 w-3.5" />
                )}
                Send
              </Button>
            </div>
          </div>
        )}
      </CardContent>

      <CardFooter className="flex items-center justify-between gap-2 border-t pt-3">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            disabled={!canDraft || isAIDrafting || isDrafting}
            onClick={() => onAIDraft(post.id)}
            title={
              post.status === 'COMMENTED'
                ? 'Post udah di-commented'
                : unsupported
                  ? `Tipe ${post.unsupported_kind} gak bisa dikomen via bot`
                  : !isAdmin
                    ? 'Butuh role admin'
                    : 'Generate draft pake AI (konteks post + template)'
            }
          >
            {isAIDrafting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wand2 className="h-3.5 w-3.5" />
            )}
            <span className="sr-only">AI Draft</span>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={!canDraft || isDrafting || isAIDrafting || draftOpen}
            onClick={() => onStartDraft(post.id)}
            title={
              post.status === 'COMMENTED'
                ? 'Post udah di-commented'
                : unsupported
                  ? `Tipe ${post.unsupported_kind} gak bisa dikomen via bot`
                  : !isAdmin
                    ? 'Butuh role admin'
                    : 'Generate draft komen'
            }
          >
            {isDrafting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            )}
            {post.status === 'DRAFTED' ? 'Re-draft' : 'Generate Draft'}
          </Button>
          {post.status !== 'SKIPPED' && (
            <Button
              variant="ghost"
              size="sm"
              disabled={!canSkip || isSkipping}
              onClick={() => onSkip(post.id)}
            >
              {isSkipping ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Skip
            </Button>
          )}
        </div>
        {post.post_url ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.open(post.post_url!, '_blank', 'noopener,noreferrer')}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  )
}

export default function Trending() {
  const role = useAuthStore((s) => s.role)
  const isAdmin = role === 'admin'

  const [sort, setSort] = useState<SortKey>('score')
  const [status, setStatus] = useState<StatusKey>('ALL')
  const [sourceId, setSourceId] = useState<'ALL' | number>('ALL')

  // Inline draft state: which post is being edited, and the current text.
  const [draftingPostId, setDraftingPostId] = useState<number | null>(null)
  const [draftText, setDraftText] = useState('')

  const qc = useQueryClient()

  const sourcesQuery = useQuery<{ sources: SourceRow[]; total: number }>({
    queryKey: ['sources-compact'],
    queryFn: () => api.listSources(false),
    staleTime: 60_000,
    enabled: isAdmin,
  })

  const trendingQuery = useQuery<TrendingResponse>({
    queryKey: ['trending', sort, status, sourceId],
    queryFn: () =>
      api.listTrending({
        sort,
        status: status === 'ALL' ? undefined : status,
        source_id: sourceId === 'ALL' ? undefined : sourceId,
        limit: 50,
      }),
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
  })

  const quotaQuery = useQuery<RateLimitResponse>({
    queryKey: ['rate-limit-status'],
    queryFn: () => api.getRateLimitStatus(),
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
  })

  const draftMutation = useMutation<DraftResponse, Error, number>({
    mutationFn: (postId) => api.generateDraft(postId),
    onSuccess: (data) => {
      setDraftingPostId(data.post.id)
      setDraftText(data.draft_text)
      qc.invalidateQueries({ queryKey: ['trending'] })
    },
    onError: (err) => {
      toast.error(err.message || 'Gagal generate draft')
    },
  })

  const aiDraftMutation = useMutation<
    { draft_text: string; post_id: number },
    Error,
    number
  >({
    mutationFn: (postId) => api.generateAIDraft(postId),
    onSuccess: (data) => {
      // Dirty-check: if the user has modified the draft text for this
      // post, confirm before overwriting their edits.
      if (
        draftingPostId === data.post_id &&
        draftText.trim().length > 0 &&
        !window.confirm(
          'Draft lu udah diedit. Overwrite dengan hasil AI?',
        )
      ) {
        return
      }
      setDraftingPostId(data.post_id)
      setDraftText(data.draft_text)
      toast.success('Draft AI siap — review dulu sebelum Send')
    },
    onError: (err) => {
      toast.error(err.message || 'Gagal generate AI draft')
    },
  })

  const skipMutation = useMutation<unknown, Error, number>({
    mutationFn: (postId) => api.skipTrendingPost(postId),
    onSuccess: () => {
      toast.success('Post di-skip')
      qc.invalidateQueries({ queryKey: ['trending'] })
    },
    onError: (err) => {
      toast.error(err.message || 'Gagal skip')
    },
  })

  const sendMutation = useMutation<
    SendCommentResponse,
    Error,
    { postId: number; text: string }
  >({
    mutationFn: ({ postId, text }) => api.sendComment(postId, text),
    onSuccess: (data) => {
      toast.success(`Komen terkirim ke ${data.post.author_name || 'post'}`)
      setDraftingPostId(null)
      setDraftText('')
      qc.invalidateQueries({ queryKey: ['trending'] })
      qc.invalidateQueries({ queryKey: ['rate-limit-status'] })
    },
    onError: (err) => {
      toast.error(err.message || 'Gagal kirim komen')
      qc.invalidateQueries({ queryKey: ['rate-limit-status'] })
    },
  })

  const posts = trendingQuery.data?.posts ?? []
  const total = trendingQuery.data?.total ?? 0
  const sources = sourcesQuery.data?.sources ?? []
  const quota = quotaQuery.data?.quota

  const sendDisabled = !isAdmin || !quota || !quota.allowed
  const sendDisabledReason = !isAdmin
    ? 'Butuh role admin buat kirim komen'
    : !quota
      ? 'Memeriksa quota...'
      : !quota.allowed
        ? `Quota habis — reset ${
            quota.resets_at
              ? formatRelative(quota.resets_at)
              : 'nanti'
          }`
        : ''

  const lastUpdatedLabel = useMemo(() => {
    if (!trendingQuery.dataUpdatedAt) return '—'
    return formatRelative(new Date(trendingQuery.dataUpdatedAt).toISOString())
  }, [trendingQuery.dataUpdatedAt])

  // When filters change, close any open draft since the post may not be in
  // the current page anymore.
  useEffect(() => {
    setDraftingPostId(null)
    setDraftText('')
  }, [sort, status, sourceId])

  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
              <TrendingUp className="h-6 w-6" />
              Trending
            </h1>
            <p className="text-muted-foreground text-sm">
              Post lagi rame dari sumber yang aktif. Auto-refresh tiap 30 detik.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-muted-foreground hidden text-xs sm:inline">
              update: {lastUpdatedLabel}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => trendingQuery.refetch()}
              disabled={trendingQuery.isFetching}
            >
              <RefreshCw
                className={cn(
                  'mr-1.5 h-3.5 w-3.5',
                  trendingQuery.isFetching && 'animate-spin',
                )}
              />
              Refresh
            </Button>
          </div>
        </div>

        <Card className="p-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Gauge
                className={cn(
                  'h-3.5 w-3.5',
                  quota && !quota.allowed
                    ? 'text-destructive'
                    : 'text-muted-foreground',
                )}
              />
              <span className="text-xs font-medium">
                Quota komen
              </span>
            </div>
            <div className="text-muted-foreground flex flex-wrap items-center gap-x-2 text-xs">
              {quota ? (
                <>
                  <span
                    className={cn(
                      'font-medium',
                      !quota.allowed && 'text-destructive',
                    )}
                  >
                    {quota.used}/{quota.limit}
                  </span>
                  <span>dalam {quota.window_hours} jam</span>
                  {quota.resets_at && (
                    <>
                      <span className="opacity-50">•</span>
                      <span>reset {formatRelative(quota.resets_at)}</span>
                    </>
                  )}
                </>
              ) : (
                <span>memuat...</span>
              )}
            </div>
            {quota && !quota.allowed && (
              <span className="text-destructive ml-auto text-xs font-medium">
                Send diblok sampai quota reset
              </span>
            )}
          </div>
        </Card>

        <Card className="p-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">Urutkan</span>
              <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
                <SelectTrigger className="h-8 w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="score">Score</SelectItem>
                  <SelectItem value="velocity">Velocity</SelectItem>
                  <SelectItem value="recent">Terbaru</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">Status</span>
              <Select value={status} onValueChange={(v) => setStatus(v as StatusKey)}>
                <SelectTrigger className="h-8 w-[130px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">Semua</SelectItem>
                  <SelectItem value="NEW">Baru</SelectItem>
                  <SelectItem value="DRAFTED">Drafted</SelectItem>
                  <SelectItem value="SKIPPED">Skipped</SelectItem>
                  <SelectItem value="COMMENTED">Commented</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {isAdmin && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground text-xs">Sumber</span>
                <Select
                  value={sourceId === 'ALL' ? 'ALL' : String(sourceId)}
                  onValueChange={(v) =>
                    setSourceId(v === 'ALL' ? 'ALL' : Number(v))
                  }
                >
                  <SelectTrigger className="h-8 w-[180px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL">Semua sumber</SelectItem>
                    {sources.map((s) => (
                      <SelectItem key={s.id} value={String(s.id)}>
                        {s.label} ({s.type})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="text-muted-foreground ml-auto text-xs">
              {total} post
            </div>
          </div>
        </Card>

        {trendingQuery.isLoading ? (
          <div className="text-muted-foreground flex items-center justify-center gap-2 py-24 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading trending posts…
          </div>
        ) : trendingQuery.isError ? (
          <Card className="p-6">
            <p className="text-destructive text-sm">
              Gagal ambil data trending:{' '}
              {(trendingQuery.error as Error)?.message || 'unknown'}
            </p>
          </Card>
        ) : posts.length === 0 ? (
          <Card className="p-10">
            <div className="text-center">
              <TrendingUp className="text-muted-foreground mx-auto mb-3 h-10 w-10" />
              <p className="text-sm font-medium">Belum ada trending post</p>
              <p className="text-muted-foreground mt-1 text-xs">
                Scanner jalan tiap 15 menit. Pastikan ada sumber aktif dan cookie
                akun masih valid.
              </p>
            </div>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {posts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                isAdmin={isAdmin}
                activeDraft={draftingPostId === post.id ? draftText : null}
                onStartDraft={(pid) => draftMutation.mutate(pid)}
                onCancelDraft={() => {
                  setDraftingPostId(null)
                  setDraftText('')
                }}
                onDraftChange={setDraftText}
                onSkip={(pid) => skipMutation.mutate(pid)}
                onSend={(pid, text) =>
                  sendMutation.mutate({ postId: pid, text })
                }
                onAIDraft={(pid) => aiDraftMutation.mutate(pid)}
                isDrafting={
                  draftMutation.isPending &&
                  draftMutation.variables === post.id
                }
                isAIDrafting={
                  aiDraftMutation.isPending &&
                  aiDraftMutation.variables === post.id
                }
                isSkipping={
                  skipMutation.isPending &&
                  skipMutation.variables === post.id
                }
                isSending={
                  sendMutation.isPending &&
                  sendMutation.variables?.postId === post.id
                }
                sendDisabled={sendDisabled}
                sendDisabledReason={sendDisabledReason}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
