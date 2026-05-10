import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Globe,
  Loader2,
  Pencil,
  Plus,
  Rss,
  Trash2,
  Users,
  X,
} from 'lucide-react'
import { toast } from 'sonner'

import { api } from '../services/api'
import { AppHeader } from '@/components/app-header'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

type SourceType = 'home_feed' | 'group' | 'page'

interface Source {
  id: number
  type: SourceType
  label: string
  url: string | null
  fb_entity_id: string | null
  keywords_include: string[]
  keywords_exclude: string[]
  enabled: boolean
  last_scanned_at: string | null
  created_at: string | null
}

interface FormState {
  type: SourceType
  label: string
  url: string
  fb_entity_id: string
  keywords_include: string[]
  keywords_exclude: string[]
  enabled: boolean
}

const emptyForm: FormState = {
  type: 'group',
  label: '',
  url: '',
  fb_entity_id: '',
  keywords_include: [],
  keywords_exclude: [],
  enabled: true,
}

const typeCopy: Record<SourceType, { label: string; hint: string; icon: React.ComponentType<{ className?: string }> }> = {
  home_feed: {
    label: 'Home Feed',
    hint: 'News feed akun lo sendiri. Cuma bisa satu.',
    icon: Rss,
  },
  group: {
    label: 'Group',
    hint: 'Grup Facebook publik/private yang lo ikutin.',
    icon: Users,
  },
  page: {
    label: 'Page',
    hint: 'Page Facebook brand/kreator.',
    icon: Globe,
  },
}

function extractFbEntityId(type: SourceType, url: string): string {
  if (type === 'home_feed' || !url) return ''
  const trimmed = url.trim()
  const groupMatch = trimmed.match(/facebook\.com\/groups\/([^/?#]+)/i)
  if (groupMatch) return groupMatch[1]
  const pageMatch = trimmed.match(/facebook\.com\/([^/?#]+)/i)
  if (pageMatch) return pageMatch[1]
  return ''
}

function TypeIcon({ type, className }: { type: SourceType; className?: string }) {
  const Icon = typeCopy[type].icon
  return <Icon className={className} />
}

function KeywordChipInput({
  label,
  placeholder,
  values,
  onChange,
}: {
  label: string
  placeholder: string
  values: string[]
  onChange: (next: string[]) => void
}) {
  const [draft, setDraft] = useState('')

  const add = () => {
    const normalized = draft.trim().toLowerCase()
    if (!normalized) return
    if (values.includes(normalized)) {
      setDraft('')
      return
    }
    onChange([...values, normalized])
    setDraft('')
  }

  const remove = (idx: number) => {
    onChange(values.filter((_, i) => i !== idx))
  }

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
            if (e.key === 'Backspace' && !draft && values.length) {
              remove(values.length - 1)
            }
          }}
          placeholder={placeholder}
        />
        <Button type="button" variant="outline" onClick={add}>
          Tambah
        </Button>
      </div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {values.map((kw, idx) => (
            <Badge
              key={`${kw}-${idx}`}
              variant="secondary"
              className="gap-1 pr-1.5"
            >
              <span>{kw}</span>
              <button
                type="button"
                onClick={() => remove(idx)}
                className="hover:bg-background/60 rounded-full p-0.5"
                aria-label={`Hapus ${kw}`}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Sources() {
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['sources'],
    queryFn: () => api.listSources(),
  })

  const sources: Source[] = data?.sources ?? []

  const hasHomeFeed = useMemo(
    () => sources.some((s) => s.type === 'home_feed'),
    [sources],
  )

  const createMutation = useMutation({
    mutationFn: (payload: FormState) =>
      api.createSource({
        type: payload.type,
        label: payload.label,
        url: payload.type === 'home_feed' ? null : payload.url || null,
        fb_entity_id:
          payload.type === 'home_feed' ? null : payload.fb_entity_id || null,
        keywords_include: payload.keywords_include,
        keywords_exclude: payload.keywords_exclude,
        enabled: payload.enabled,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      toast.success('Sumber tersimpan')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal simpan sumber'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: FormState }) =>
      api.updateSource(id, {
        label: payload.label,
        url: payload.type === 'home_feed' ? null : payload.url || null,
        fb_entity_id:
          payload.type === 'home_feed' ? null : payload.fb_entity_id || null,
        keywords_include: payload.keywords_include,
        keywords_exclude: payload.keywords_exclude,
        enabled: payload.enabled,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      toast.success('Sumber diperbarui')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal update sumber'),
  })

  const toggleMutation = useMutation({
    mutationFn: (id: number) => api.toggleSource(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sources'] })
    },
    onError: (err: any) => toast.error(err.message || 'Gagal toggle sumber'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSource(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      toast.success('Sumber dihapus')
      setConfirmDeleteId(null)
    },
    onError: (err: any) => toast.error(err.message || 'Gagal hapus sumber'),
  })

  useEffect(() => {
    if (!dialogOpen) {
      setForm(emptyForm)
      setEditingId(null)
    }
  }, [dialogOpen])

  const openCreateDialog = () => {
    setEditingId(null)
    setForm({
      ...emptyForm,
      type: hasHomeFeed ? 'group' : 'group',
    })
    setDialogOpen(true)
  }

  const openEditDialog = (source: Source) => {
    setEditingId(source.id)
    setForm({
      type: source.type,
      label: source.label,
      url: source.url ?? '',
      fb_entity_id: source.fb_entity_id ?? '',
      keywords_include: source.keywords_include,
      keywords_exclude: source.keywords_exclude,
      enabled: source.enabled,
    })
    setDialogOpen(true)
  }

  const closeDialog = () => setDialogOpen(false)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.label.trim()) {
      toast.error('Label wajib diisi')
      return
    }
    if (form.type !== 'home_feed' && !form.url.trim()) {
      toast.error('URL wajib diisi untuk group/page')
      return
    }
    if (editingId != null) {
      updateMutation.mutate({ id: editingId, payload: form })
    } else {
      createMutation.mutate(form)
    }
  }

  const handleTypeChange = (next: SourceType) => {
    setForm((f) => {
      if (next === 'home_feed') {
        return { ...f, type: next, url: '', fb_entity_id: '' }
      }
      return { ...f, type: next }
    })
  }

  const handleUrlBlur = () => {
    if (form.type === 'home_feed') return
    if (form.fb_entity_id) return
    const extracted = extractFbEntityId(form.type, form.url)
    if (extracted) {
      setForm((f) => ({ ...f, fb_entity_id: extracted }))
    }
  }

  const isSubmitting =
    createMutation.isPending || updateMutation.isPending

  return (
    <div className="bg-background min-h-screen">
      <AppHeader />

      <main className="mx-auto max-w-5xl space-y-6 p-4 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">Sumber Scan</h1>
            <p className="text-muted-foreground text-sm">
              Daftar feed/grup/page yang di-scan scanner tiap 15 menit. Matiin
              yang lagi gak dipake biar scan fokus.
            </p>
          </div>
          <Button onClick={openCreateDialog}>
            <Plus className="size-4" />
            Tambah Sumber
          </Button>
        </div>

        {isLoading && (
          <Card>
            <CardContent className="text-muted-foreground py-12 text-center text-sm">
              Lagi muat sumber…
            </CardContent>
          </Card>
        )}

        {!isLoading && sources.length === 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Belum ada sumber</CardTitle>
              <CardDescription>
                Tambahin minimal satu sumber biar scanner punya tempat buat
                scraping. Home feed akun lo sendiri biasanya paling rich.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={openCreateDialog}>
                <Plus className="size-4" />
                Tambah Sumber Pertama
              </Button>
            </CardContent>
          </Card>
        )}

        {!isLoading && sources.length > 0 && (
          <div className="grid gap-3">
            {sources.map((source) => {
              const meta = typeCopy[source.type]
              return (
                <Card
                  key={source.id}
                  className={cn(
                    !source.enabled && 'opacity-60',
                  )}
                >
                  <CardHeader>
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 min-w-0">
                        <div className="bg-muted text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-full">
                          <TypeIcon type={source.type} className="size-5" />
                        </div>
                        <div className="min-w-0 space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <CardTitle className="text-base">
                              {source.label}
                            </CardTitle>
                            <Badge variant="outline" className="gap-1">
                              <TypeIcon
                                type={source.type}
                                className="size-3"
                              />
                              {meta.label}
                            </Badge>
                            {source.enabled ? (
                              <Badge variant="success">Aktif</Badge>
                            ) : (
                              <Badge variant="outline">Nonaktif</Badge>
                            )}
                          </div>
                          <CardDescription className="break-all">
                            {source.url ?? 'Home feed akun lo'}
                          </CardDescription>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Switch
                          checked={source.enabled}
                          onCheckedChange={() =>
                            toggleMutation.mutate(source.id)
                          }
                          aria-label="Toggle aktif"
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => openEditDialog(source)}
                          aria-label="Edit sumber"
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setConfirmDeleteId(source.id)}
                          aria-label="Hapus sumber"
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  {(source.keywords_include.length > 0 ||
                    source.keywords_exclude.length > 0) && (
                    <CardContent className="space-y-2">
                      {source.keywords_include.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-muted-foreground text-xs font-medium">
                            Include:
                          </span>
                          {source.keywords_include.map((kw) => (
                            <Badge key={`in-${kw}`} variant="secondary">
                              {kw}
                            </Badge>
                          ))}
                        </div>
                      )}
                      {source.keywords_exclude.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-muted-foreground text-xs font-medium">
                            Exclude:
                          </span>
                          {source.keywords_exclude.map((kw) => (
                            <Badge key={`ex-${kw}`} variant="destructive">
                              {kw}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  )}
                </Card>
              )
            })}
          </div>
        )}
      </main>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <form onSubmit={handleSubmit} className="space-y-5">
            <DialogHeader>
              <DialogTitle>
                {editingId != null ? 'Edit Sumber' : 'Tambah Sumber'}
              </DialogTitle>
              <DialogDescription>
                Tentuin tipe sumber, label internal, dan filter keyword
                (opsional).
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-2">
              <Label htmlFor="source-type">Tipe</Label>
              <Select
                value={form.type}
                onValueChange={(v) => handleTypeChange(v as SourceType)}
                disabled={editingId != null}
              >
                <SelectTrigger id="source-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem
                    value="home_feed"
                    disabled={hasHomeFeed && editingId == null}
                  >
                    Home Feed {hasHomeFeed && editingId == null ? '(udah ada)' : ''}
                  </SelectItem>
                  <SelectItem value="group">Group</SelectItem>
                  <SelectItem value="page">Page</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                {typeCopy[form.type].hint}
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="source-label">Label</Label>
              <Input
                id="source-label"
                value={form.label}
                onChange={(e) =>
                  setForm((f) => ({ ...f, label: e.target.value }))
                }
                placeholder="Nama buat ngenalin sumber, contoh: Grup Jualan Laptop"
                required
              />
            </div>

            {form.type !== 'home_feed' && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="source-url">URL Facebook</Label>
                  <Input
                    id="source-url"
                    value={form.url}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, url: e.target.value }))
                    }
                    onBlur={handleUrlBlur}
                    placeholder={
                      form.type === 'group'
                        ? 'https://www.facebook.com/groups/123456789'
                        : 'https://www.facebook.com/NamaPage'
                    }
                    required={form.type !== 'home_feed'}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="source-entity">FB Entity ID</Label>
                  <Input
                    id="source-entity"
                    value={form.fb_entity_id}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, fb_entity_id: e.target.value }))
                    }
                    placeholder={
                      form.type === 'group' ? '123456789' : 'NamaPage'
                    }
                  />
                  <p className="text-muted-foreground text-xs">
                    Otomatis ke-parse dari URL kalo kosong. Edit manual kalo
                    beda.
                  </p>
                </div>
              </>
            )}

            <KeywordChipInput
              label="Keyword Include (wajib match minimal 1)"
              placeholder="Ketik keyword, Enter buat tambah"
              values={form.keywords_include}
              onChange={(next) =>
                setForm((f) => ({ ...f, keywords_include: next }))
              }
            />

            <KeywordChipInput
              label="Keyword Exclude (skip kalo kena)"
              placeholder="Ketik keyword, Enter buat tambah"
              values={form.keywords_exclude}
              onChange={(next) =>
                setForm((f) => ({ ...f, keywords_exclude: next }))
              }
            />

            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="space-y-0.5">
                <Label htmlFor="source-enabled" className="cursor-pointer">
                  Aktif
                </Label>
                <p className="text-muted-foreground text-xs">
                  Kalo nonaktif, sumber ini di-skip scanner sampe lo nyalain
                  lagi.
                </p>
              </div>
              <Switch
                id="source-enabled"
                checked={form.enabled}
                onCheckedChange={(checked) =>
                  setForm((f) => ({ ...f, enabled: checked }))
                }
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={closeDialog}
                disabled={isSubmitting}
              >
                Batal
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && <Loader2 className="size-4 animate-spin" />}
                {editingId != null ? 'Simpan' : 'Tambah'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={confirmDeleteId != null}
        onOpenChange={(open) => !open && setConfirmDeleteId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus sumber ini?</AlertDialogTitle>
            <AlertDialogDescription>
              Semua trending post yang nempel ke sumber ini juga ke-hapus
              (CASCADE). Aksi ini gak bisa di-undo.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (confirmDeleteId != null) {
                  deleteMutation.mutate(confirmDeleteId)
                }
              }}
              className="bg-destructive hover:bg-destructive/90"
            >
              Hapus
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
