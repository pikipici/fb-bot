import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  Cookie,
  KeyRound,
  Loader2,
  Lock,
  Pencil,
  RotateCw,
  ShieldAlert,
  Trash2,
  UserPlus,
} from 'lucide-react'
import { toast } from 'sonner'

import { api } from '../services/api'
import { AppHeader } from '@/components/app-header'
import { CookieInstructions } from '@/components/CookieInstructions'
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
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'

interface FBAccount {
  id: number
  label: string
  email: string | null
  status: string
  purpose: string
  last_used_at: string | null
  cooldown_until: string | null
  failure_count: number
  total_uses: number
  notes: string | null
  created_at: string
  fb_user_id: string | null
  fb_name: string | null
  fb_profile_pic_url: string | null
  cookies_expired_at: string | null
  has_cookies: boolean
}

interface CookiePreview {
  fb_user_id: string
  name: string
  profile_pic_url: string | null
}

type ManualForm = {
  label: string
  email: string
  password: string
  notes: string
}

const initialManualForm: ManualForm = {
  label: '',
  email: '',
  password: '',
  notes: '',
}

const statusBadgeVariant: Record<string, React.ComponentProps<typeof Badge>['variant']> = {
  ACTIVE: 'success',
  COOLDOWN: 'warning',
  BLOCKED: 'destructive',
  DISABLED: 'outline',
  EXPIRED: 'destructive',
}

export default function FBAccounts() {
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [mode, setMode] = useState<'setup' | 'edit'>('setup')
  const [setupTab, setSetupTab] = useState<'cookie' | 'manual'>('cookie')
  const [manualForm, setManualForm] = useState<ManualForm>(initialManualForm)
  const [cookieLabel, setCookieLabel] = useState('')
  const [cookieRaw, setCookieRaw] = useState('')
  const [cookiePreview, setCookiePreview] = useState<CookiePreview | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['fbAccountCurrent'],
    queryFn: () => api.getCurrentFBAccount(),
  })

  const account: FBAccount | null = data?.account ?? null

  const createManualMutation = useMutation({
    mutationFn: (payload: ManualForm) =>
      api.createFBAccount({ ...payload, purpose: 'both' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun tersimpan')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal menyimpan akun'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<ManualForm> }) =>
      api.updateFBAccount(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun diperbarui')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal update akun'),
  })

  const previewCookieMutation = useMutation({
    mutationFn: (raw: string) => api.previewFBCookie(raw),
    onSuccess: (data) => {
      setCookiePreview(data.preview)
      toast.success('Cookie valid, cek preview di bawah')
    },
    onError: (err: any) => {
      setCookiePreview(null)
      toast.error(err.message || 'Cookie gak valid')
    },
  })

  const connectCookieMutation = useMutation({
    mutationFn: ({ label, raw }: { label: string; raw: string }) =>
      api.connectFBCookie({ label, raw_cookies: raw }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun tersambung')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal connect akun'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteFBAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun dihapus')
      setConfirmDelete(false)
    },
    onError: (err: any) => toast.error(err.message || 'Gagal hapus akun'),
  })

  const reactivateMutation = useMutation({
    mutationFn: (id: number) => api.reactivateFBAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun diaktifkan ulang')
    },
    onError: (err: any) => toast.error(err.message || 'Gagal reactivate akun'),
  })

  useEffect(() => {
    if (!dialogOpen) return
    if (mode === 'edit' && account) {
      setManualForm({
        label: account.label,
        email: account.email ?? '',
        password: '',
        notes: account.notes ?? '',
      })
    } else {
      setManualForm(initialManualForm)
      setCookieLabel('')
      setCookieRaw('')
      setCookiePreview(null)
      setSetupTab('cookie')
    }
  }, [dialogOpen, mode, account])

  function openSetup() {
    setMode('setup')
    setDialogOpen(true)
  }

  function openEdit() {
    if (!account) return
    setMode('edit')
    setDialogOpen(true)
  }

  function closeDialog() {
    setDialogOpen(false)
    setManualForm(initialManualForm)
    setCookieLabel('')
    setCookieRaw('')
    setCookiePreview(null)
  }

  function handleManualSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (mode === 'edit' && account) {
      const payload: Partial<ManualForm> = {}
      if (manualForm.label) payload.label = manualForm.label
      if (manualForm.email) payload.email = manualForm.email
      if (manualForm.password) payload.password = manualForm.password
      payload.notes = manualForm.notes
      updateMutation.mutate({ id: account.id, payload })
    } else {
      createManualMutation.mutate(manualForm)
    }
  }

  function handleCookiePreview() {
    if (!cookieRaw.trim()) {
      toast.error('Paste cookie dulu')
      return
    }
    previewCookieMutation.mutate(cookieRaw)
  }

  function handleCookieConnect() {
    if (!cookieLabel.trim()) {
      toast.error('Label wajib diisi')
      return
    }
    if (!cookiePreview) {
      toast.error('Preview dulu cookie-nya biar yakin valid')
      return
    }
    connectCookieMutation.mutate({ label: cookieLabel, raw: cookieRaw })
  }

  const manualSubmitting =
    createManualMutation.isPending || updateMutation.isPending
  const connecting = connectCookieMutation.isPending

  const isCookieAccount = account?.has_cookies
  const displayName = account
    ? account.fb_name || account.email || account.label
    : ''

  return (
    <div className="bg-background min-h-screen">
      <AppHeader />

      <main className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="mb-6">
          <h2 className="text-2xl font-semibold tracking-tight">Facebook Account</h2>
          <p className="text-muted-foreground text-sm">
            Akun Facebook yang dipakai scanner dan comment assistant. Hanya satu
            akun yang bisa tersimpan.
          </p>
        </div>

        {isLoading && (
          <Card className="flex items-center justify-center py-12">
            <Loader2 className="text-muted-foreground h-5 w-5 animate-spin" />
          </Card>
        )}

        {!isLoading && !account && (
          <Card>
            <CardHeader className="items-center text-center">
              <div className="bg-muted text-muted-foreground mb-2 flex size-12 items-center justify-center rounded-full">
                <Lock className="size-5" />
              </div>
              <CardTitle>Belum ada akun</CardTitle>
              <CardDescription>
                Connect akun Facebook lu pake cookie session atau kredensial
                manual.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex justify-center">
              <Button onClick={openSetup} size="lg">
                <UserPlus />
                Setup Account
              </Button>
            </CardContent>
          </Card>
        )}

        {!isLoading && account && (
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  {account.fb_profile_pic_url ? (
                    <img
                      src={account.fb_profile_pic_url}
                      alt={displayName}
                      className="size-12 rounded-full border"
                      referrerPolicy="no-referrer"
                    />
                  ) : (
                    <div className="bg-muted text-muted-foreground flex size-12 items-center justify-center rounded-full">
                      <UserPlus className="size-5" />
                    </div>
                  )}
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <CardTitle className="text-base">
                        {displayName}
                      </CardTitle>
                      <Badge variant={statusBadgeVariant[account.status] ?? 'outline'}>
                        {account.status}
                      </Badge>
                      {isCookieAccount && (
                        <Badge variant="outline" className="gap-1">
                          <Cookie className="size-3" />
                          Cookie
                        </Badge>
                      )}
                    </div>
                    <CardDescription>
                      {account.label}
                      {account.fb_user_id ? ` · FB ID ${account.fb_user_id}` : ''}
                    </CardDescription>
                  </div>
                </div>

                <div className="flex shrink-0 flex-wrap gap-2">
                  {account.status === 'BLOCKED' && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => reactivateMutation.mutate(account.id)}
                      disabled={reactivateMutation.isPending}
                    >
                      <RotateCw />
                      Reactivate
                    </Button>
                  )}
                  {!isCookieAccount && (
                    <Button size="sm" variant="outline" onClick={openEdit}>
                      <Pencil />
                      Edit
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Trash2 />
                    Hapus
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <dl className="text-muted-foreground grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
                <div>
                  <dt className="text-foreground font-medium">Uses</dt>
                  <dd>{account.total_uses}</dd>
                </div>
                <div>
                  <dt className="text-foreground font-medium">Failures</dt>
                  <dd>{account.failure_count}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-foreground font-medium">Last used</dt>
                  <dd>
                    {account.last_used_at
                      ? new Date(account.last_used_at).toLocaleString()
                      : '—'}
                  </dd>
                </div>
              </dl>
              {account.notes && (
                <p className="text-muted-foreground border-t pt-3 text-xs italic">
                  {account.notes}
                </p>
              )}
              {account.status === 'EXPIRED' && (
                <div className="text-destructive flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs">
                  <ShieldAlert className="size-4 shrink-0" />
                  <span>
                    Cookie lu udah expired. Hapus akun ini dan connect ulang
                    pake cookie yang fresh.
                  </span>
                </div>
              )}
              {account.status === 'BLOCKED' && (
                <div className="text-destructive flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs">
                  <ShieldAlert className="size-4 shrink-0" />
                  <span>
                    Akun terblokir oleh Facebook. Reactivate buat coba lagi,
                    atau hapus dan daftarkan yang baru.
                  </span>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </main>

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => (open ? setDialogOpen(true) : closeDialog())}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {mode === 'edit' ? 'Edit Account' : 'Setup Account'}
            </DialogTitle>
            <DialogDescription>
              {mode === 'edit'
                ? 'Perbarui data akun. Kosongkan password kalau mau tetap pakai yang lama.'
                : 'Pilih cara connect yang paling cocok buat lu.'}
            </DialogDescription>
          </DialogHeader>

          {mode === 'edit' ? (
            <ManualForm
              form={manualForm}
              setForm={setManualForm}
              onSubmit={handleManualSubmit}
              onCancel={closeDialog}
              submitting={manualSubmitting}
              submitLabel="Simpan"
              isEdit
            />
          ) : (
            <Tabs
              value={setupTab}
              onValueChange={(v) => setSetupTab(v as 'cookie' | 'manual')}
            >
              <TabsList className="w-full">
                <TabsTrigger value="cookie" className="flex-1">
                  <Cookie />
                  Cookie (Direkomendasikan)
                </TabsTrigger>
                <TabsTrigger value="manual" className="flex-1">
                  <KeyRound />
                  Manual
                </TabsTrigger>
              </TabsList>

              <TabsContent value="cookie" className="mt-4 space-y-4">
                <CookieInstructions />

                <div className="space-y-2">
                  <Label htmlFor="cookie-label">Label</Label>
                  <Input
                    id="cookie-label"
                    value={cookieLabel}
                    onChange={(e) => setCookieLabel(e.target.value)}
                    placeholder="e.g. Main FB"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cookie-raw">Cookie String</Label>
                  <Textarea
                    id="cookie-raw"
                    value={cookieRaw}
                    onChange={(e) => {
                      setCookieRaw(e.target.value)
                      setCookiePreview(null)
                    }}
                    placeholder="c_user=...; xs=...; datr=...; fr=...;"
                    className="min-h-28 font-mono text-xs"
                  />
                  <p className="text-muted-foreground text-xs">
                    Format Header String dari Cookie-Editor. Jangan paste format
                    JSON.
                  </p>
                </div>

                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleCookiePreview}
                    disabled={previewCookieMutation.isPending}
                  >
                    {previewCookieMutation.isPending && (
                      <Loader2 className="animate-spin" />
                    )}
                    Preview Akun
                  </Button>
                </div>

                {cookiePreview && (
                  <div className="bg-muted/40 flex items-center gap-3 rounded-md border p-3">
                    {cookiePreview.profile_pic_url ? (
                      <img
                        src={cookiePreview.profile_pic_url}
                        alt={cookiePreview.name}
                        className="size-12 rounded-full border"
                        referrerPolicy="no-referrer"
                      />
                    ) : (
                      <div className="bg-muted text-muted-foreground flex size-12 items-center justify-center rounded-full">
                        <UserPlus className="size-5" />
                      </div>
                    )}
                    <div className="flex-1">
                      <p className="flex items-center gap-2 text-sm font-medium">
                        <CheckCircle2 className="size-4 text-green-500" />
                        {cookiePreview.name}
                      </p>
                      <p className="text-muted-foreground text-xs">
                        FB ID {cookiePreview.fb_user_id}
                      </p>
                    </div>
                  </div>
                )}

                <DialogFooter>
                  <Button variant="outline" onClick={closeDialog}>
                    Batal
                  </Button>
                  <Button
                    onClick={handleCookieConnect}
                    disabled={!cookiePreview || connecting}
                  >
                    {connecting && <Loader2 className="animate-spin" />}
                    Simpan
                  </Button>
                </DialogFooter>
              </TabsContent>

              <TabsContent value="manual" className="mt-4">
                <ManualForm
                  form={manualForm}
                  setForm={setManualForm}
                  onSubmit={handleManualSubmit}
                  onCancel={closeDialog}
                  submitting={manualSubmitting}
                  submitLabel="Tambah Akun"
                />
              </TabsContent>
            </Tabs>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={confirmDelete}
        onOpenChange={(open) => !open && setConfirmDelete(false)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus akun?</AlertDialogTitle>
            <AlertDialogDescription>
              Akun{' '}
              <span className="text-foreground font-medium">
                {account?.label}
              </span>{' '}
              akan dihapus permanen. Setelahnya lu perlu setup ulang untuk
              scanner dan comment assistant.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              Batal
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
              onClick={(e) => {
                e.preventDefault()
                if (account) deleteMutation.mutate(account.id)
              }}
            >
              {deleteMutation.isPending && <Loader2 className="animate-spin" />}
              Hapus
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ManualForm({
  form,
  setForm,
  onSubmit,
  onCancel,
  submitting,
  submitLabel,
  isEdit = false,
}: {
  form: ManualForm
  setForm: (f: ManualForm) => void
  onSubmit: (e: React.FormEvent) => void
  onCancel: () => void
  submitting: boolean
  submitLabel: string
  isEdit?: boolean
}) {
  return (
    <form onSubmit={onSubmit} className="grid gap-4">
      <div className="space-y-2">
        <Label htmlFor="label">Label</Label>
        <Input
          id="label"
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          placeholder="e.g. Main Account"
          required
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="email">Email / Phone</Label>
        <Input
          id="email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          placeholder="email@example.com"
          required={!isEdit}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="password">
          Password{' '}
          {isEdit && (
            <span className="text-muted-foreground text-xs font-normal">
              (kosongin = pake yang lama)
            </span>
          )}
        </Label>
        <Input
          id="password"
          type="password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          required={!isEdit}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="notes">Notes</Label>
        <Input
          id="notes"
          value={form.notes}
          onChange={(e) => setForm({ ...form, notes: e.target.value })}
          placeholder="Opsional..."
        />
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Batal
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting && <Loader2 className="animate-spin" />}
          {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  )
}
