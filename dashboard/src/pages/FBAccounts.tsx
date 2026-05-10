import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
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

interface FBAccount {
  id: number
  label: string
  email: string
  status: string
  purpose: string
  last_used_at: string | null
  cooldown_until: string | null
  failure_count: number
  total_uses: number
  notes: string | null
  created_at: string
}

type FormState = {
  label: string
  email: string
  password: string
  notes: string
}

const initialForm: FormState = {
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
}

export default function FBAccounts() {
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [mode, setMode] = useState<'setup' | 'edit'>('setup')
  const [form, setForm] = useState<FormState>(initialForm)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['fbAccountCurrent'],
    queryFn: () => api.getCurrentFBAccount(),
  })

  const account: FBAccount | null = data?.account ?? null

  const createMutation = useMutation({
    mutationFn: (payload: FormState) =>
      api.createFBAccount({ ...payload, purpose: 'both' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun tersimpan')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal menyimpan akun'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<FormState> }) =>
      api.updateFBAccount(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
      toast.success('Akun diperbarui')
      closeDialog()
    },
    onError: (err: any) => toast.error(err.message || 'Gagal update akun'),
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
      setForm({
        label: account.label,
        email: account.email,
        password: '',
        notes: account.notes ?? '',
      })
    } else {
      setForm(initialForm)
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
    setForm(initialForm)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (mode === 'edit' && account) {
      const payload: Partial<FormState> = {}
      if (form.label) payload.label = form.label
      if (form.email) payload.email = form.email
      if (form.password) payload.password = form.password
      payload.notes = form.notes
      updateMutation.mutate({ id: account.id, payload })
    } else {
      createMutation.mutate(form)
    }
  }

  const submitting = createMutation.isPending || updateMutation.isPending

  return (
    <div className="bg-background min-h-screen">
      <AppHeader />

      <main className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="mb-6">
          <h2 className="text-2xl font-semibold tracking-tight">Facebook Account</h2>
          <p className="text-muted-foreground text-sm">
            Akun Facebook yang dipakai scraper dan poster. Hanya satu akun yang
            bisa tersimpan pada satu waktu.
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
                Tambahkan akun Facebook untuk mulai scraping dan posting.
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
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <CardTitle className="text-base">{account.label}</CardTitle>
                    <Badge variant={statusBadgeVariant[account.status] ?? 'outline'}>
                      {account.status}
                    </Badge>
                  </div>
                  <CardDescription>{account.email}</CardDescription>
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
                  <Button size="sm" variant="outline" onClick={openEdit}>
                    <Pencil />
                    Edit
                  </Button>
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
              {account.status === 'BLOCKED' && (
                <div className="text-destructive flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs">
                  <ShieldAlert className="size-4 shrink-0" />
                  <span>
                    Akun terblokir oleh Facebook. Reactivate untuk mencoba lagi,
                    atau hapus dan daftarkan kredensial baru.
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
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {mode === 'edit' ? 'Edit Account' : 'Setup Account'}
            </DialogTitle>
            <DialogDescription>
              {mode === 'edit'
                ? 'Perbarui data akun. Kosongkan password untuk mempertahankan yang lama.'
                : 'Isi kredensial akun Facebook yang akan dipakai worker.'}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="grid gap-4">
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
                required={mode === 'setup'}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">
                Password
                {mode === 'edit' && (
                  <span className="text-muted-foreground text-xs font-normal">
                    (kosongkan = tetap pakai yang lama)
                  </span>
                )}
              </Label>
              <Input
                id="password"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required={mode === 'setup'}
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
              <Button type="button" variant="outline" onClick={closeDialog}>
                Batal
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting && <Loader2 className="animate-spin" />}
                {mode === 'edit' ? 'Simpan' : 'Tambah Akun'}
              </Button>
            </DialogFooter>
          </form>
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
              akan dihapus permanen. Setelahnya kamu perlu setup ulang kredensial
              baru untuk scraping dan posting.
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
