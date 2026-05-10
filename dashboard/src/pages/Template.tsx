import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, Loader2, Save } from 'lucide-react'
import { toast } from 'sonner'

import { api } from '../services/api'
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
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

interface TemplateRow {
  id: number
  name: string
  template_text: string
  is_active: boolean
  created_at: string | null
  updated_at: string | null
}

interface TemplateResponse {
  template: TemplateRow | null
}

const MAX_LEN = 5000
const SAMPLE_AUTHOR = 'Budi Santoso'
const SAMPLE_TEXT = 'jual laptop gaming bekas, spek gahar, minat dm'

function renderPreview(template: string): string {
  return template
    .replaceAll('{author_name}', SAMPLE_AUTHOR)
    .replaceAll('{text_snippet}', SAMPLE_TEXT)
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('id-ID', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function Template() {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')

  const templateQuery = useQuery<TemplateResponse>({
    queryKey: ['template'],
    queryFn: () => api.getTemplate(),
    staleTime: 30_000,
  })

  const saveMutation = useMutation({
    mutationFn: (text: string) => api.upsertTemplate(text),
    onSuccess: () => {
      toast.success('Template tersimpan')
      qc.invalidateQueries({ queryKey: ['template'] })
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Gagal simpan template')
    },
  })

  // Sync remote template into local draft when first loaded.
  useEffect(() => {
    if (templateQuery.data && draft === '') {
      setDraft(templateQuery.data.template?.template_text ?? '')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateQuery.data])

  const current = templateQuery.data?.template ?? null
  const dirty = useMemo(
    () => (current?.template_text ?? '') !== draft,
    [current, draft],
  )
  const trimmedEmpty = draft.trim().length === 0
  const preview = useMemo(() => renderPreview(draft), [draft])

  const canSave = dirty && !trimmedEmpty && !saveMutation.isPending

  const handleSave = () => {
    if (!canSave) return
    saveMutation.mutate(draft.trim())
  }

  const handleReset = () => {
    setDraft(current?.template_text ?? '')
  }

  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
              <FileText className="h-6 w-6" />
              Template Komen
            </h1>
            <p className="text-muted-foreground text-sm">
              Template promosi yang bakal dipake generate draft per trending post.
              Satu template aktif buat MVP.
            </p>
          </div>

          {current && (
            <div className="text-muted-foreground text-xs">
              Update terakhir: {formatDate(current.updated_at)}
            </div>
          )}
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Editor</CardTitle>
            <CardDescription>
              Pake placeholder:{' '}
              <code className="bg-muted rounded px-1 py-0.5 text-xs">
                {'{author_name}'}
              </code>{' '}
              dan{' '}
              <code className="bg-muted rounded px-1 py-0.5 text-xs">
                {'{text_snippet}'}
              </code>
              . Kosong otomatis jadi string kosong kalau post gak ada.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {templateQuery.isLoading ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading template…
              </div>
            ) : (
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value.slice(0, MAX_LEN))}
                placeholder="Contoh: Halo {author_name}, tertarik laptop gaming second? cek profil gue gan"
                className="min-h-[140px] font-mono text-sm"
                disabled={saveMutation.isPending}
              />
            )}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-muted-foreground flex items-center gap-2 text-xs">
                <span>
                  {draft.length} / {MAX_LEN} karakter
                </span>
                {dirty && <Badge variant="outline">Belum disimpan</Badge>}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleReset}
                  disabled={!dirty || saveMutation.isPending}
                >
                  Reset
                </Button>
                <Button
                  size="sm"
                  onClick={handleSave}
                  disabled={!canSave}
                  className={cn(saveMutation.isPending && 'opacity-60')}
                >
                  {saveMutation.isPending ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Simpan
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Preview</CardTitle>
            <CardDescription>
              Contoh render dengan author ={' '}
              <span className="font-medium">{SAMPLE_AUTHOR}</span>, snippet ={' '}
              <span className="font-medium">{SAMPLE_TEXT.slice(0, 40)}…</span>
            </CardDescription>
          </CardHeader>
          <CardContent>
            {preview.trim() ? (
              <div className="bg-muted rounded-md px-3 py-2 text-sm whitespace-pre-wrap break-words">
                {preview}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm italic">
                (kosong — isi template dulu untuk lihat preview)
              </p>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
