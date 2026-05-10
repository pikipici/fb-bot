import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Inbox, Loader2, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import { api } from '../services/api'
import { useAuthStore } from '../store/authStore'
import { AppHeader } from '@/components/app-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

interface Draft {
  id: number
  post_id: number
  text: string | null
  source_type: string
  status: string
  created_at: string
}

export default function ReviewQueue() {
  const role = useAuthStore((s) => s.role)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['pendingDrafts'],
    queryFn: () => api.getPendingDrafts(),
  })

  const handleAction = async (draftId: number, action: 'approve' | 'reject') => {
    try {
      await api.approveDraft(draftId, action)
      toast.success(action === 'approve' ? 'Draft approved' : 'Draft rejected')
      refetch()
    } catch (err: any) {
      toast.error(err.message || 'Action failed')
    }
  }

  const drafts: Draft[] = data?.drafts ?? []
  const canReview = role === 'operator' || role === 'admin'

  return (
    <div className="bg-background min-h-screen">
      <AppHeader />

      <main className="mx-auto max-w-4xl p-4 sm:p-6">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Review Queue</h2>
            <p className="text-muted-foreground text-sm">
              Approve or reject pending drafts before they go live.
            </p>
          </div>
          {!isLoading && drafts.length > 0 && (
            <Badge variant="secondary" className="h-6">
              {drafts.length} pending
            </Badge>
          )}
        </div>

        {isLoading && (
          <Card className="flex items-center justify-center py-12">
            <Loader2 className="text-muted-foreground h-5 w-5 animate-spin" />
          </Card>
        )}

        {!isLoading && drafts.length === 0 && (
          <Card className="py-12">
            <CardContent className="flex flex-col items-center justify-center gap-2 text-center">
              <Inbox className="text-muted-foreground h-8 w-8" />
              <p className="text-muted-foreground text-sm">
                No pending drafts to review.
              </p>
            </CardContent>
          </Card>
        )}

        <div className="space-y-3">
          {drafts.map((draft) => (
            <Card key={draft.id}>
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="uppercase">
                      {draft.source_type}
                    </Badge>
                    <CardDescription>Post #{draft.post_id}</CardDescription>
                  </div>
                  <span className="text-muted-foreground text-xs">
                    {new Date(draft.created_at).toLocaleString()}
                  </span>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <CardTitle className="text-foreground text-base leading-relaxed font-normal">
                  {draft.text || (
                    <span className="text-muted-foreground italic">
                      No draft text (needs manual write)
                    </span>
                  )}
                </CardTitle>

                {canReview && draft.text && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => handleAction(draft.id, 'approve')}
                    >
                      <CheckCircle2 />
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => handleAction(draft.id, 'reject')}
                    >
                      <XCircle />
                      Reject
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </main>
    </div>
  )
}
