import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Pencil, Plus, RotateCw, Trash2, Users } from 'lucide-react'
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
  purpose: string
  notes: string
}

const initialForm: FormState = {
  label: '',
  email: '',
  password: '',
  purpose: 'both',
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
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(initialForm)
  const [pendingDelete, setPendingDelete] = useState<FBAccount | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['fbAccounts'],
    queryFn: () => api.getFBAccounts(true),
  })

  const createMutation = useMutation({
    mutationFn: (payload: FormState) => api.createFBAccount(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccounts'] })
      toast.success('Account created')
      resetForm()
    },
    onError: (err: any) => toast.error(err.message || 'Create failed'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<FormState> }) =>
      api.updateFBAccount(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccounts'] })
      toast.success('Account updated')
      resetForm()
    },
    onError: (err: any) => toast.error(err.message || 'Update failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteFBAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccounts'] })
      toast.success('Account deleted')
      setPendingDelete(null)
    },
    onError: (err: any) => toast.error(err.message || 'Delete failed'),
  })

  const reactivateMutation = useMutation({
    mutationFn: (id: number) => api.reactivateFBAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fbAccounts'] })
      toast.success('Account reactivated')
    },
    onError: (err: any) => toast.error(err.message || 'Reactivate failed'),
  })

  function resetForm() {
    setForm(initialForm)
    setDialogOpen(false)
    setEditId(null)
  }

  function openCreate() {
    setForm(initialForm)
    setEditId(null)
    setDialogOpen(true)
  }

  function openEdit(account: FBAccount) {
    setForm({
      label: account.label,
      email: account.email,
      password: '',
      purpose: account.purpose,
      notes: account.notes || '',
    })
    setEditId(account.id)
    setDialogOpen(true)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (editId) {
      const payload: Partial<FormState> = {}
      if (form.label) payload.label = form.label
      if (form.email) payload.email = form.email
      if (form.password) payload.password = form.password
      if (form.purpose) payload.purpose = form.purpose
      payload.notes = form.notes
      updateMutation.mutate({ id: editId, payload })
    } else {
      createMutation.mutate(form)
    }
  }

  const accounts: FBAccount[] = data?.accounts ?? []
  const submitting = createMutation.isPending || updateMutation.isPending

  return (
    <div className="bg-background min-h-screen">
      <AppHeader />

      <main className="mx-auto max-w-5xl p-4 sm:p-6">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Facebook Accounts</h2>
            <p className="text-muted-foreground text-sm">
              Manage accounts used by scraper and poster workers.
            </p>
          </div>
          <Button onClick={openCreate}>
            <Plus />
            Add Account
          </Button>
        </div>

        {isLoading && (
          <Card className="flex items-center justify-center py-12">
            <Loader2 className="text-muted-foreground h-5 w-5 animate-spin" />
          </Card>
        )}

        {!isLoading && accounts.length === 0 && (
          <Card className="py-12">
            <CardContent className="flex flex-col items-center justify-center gap-2 text-center">
              <Users className="text-muted-foreground h-8 w-8" />
              <p className="text-muted-foreground text-sm">
                No Facebook accounts added yet.
              </p>
            </CardContent>
          </Card>
        )}

        <div className="space-y-3">
          {accounts.map((account) => (
            <Card key={account.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <CardTitle className="text-base">{account.label}</CardTitle>
                      <Badge variant={statusBadgeVariant[account.status] ?? 'outline'}>
                        {account.status}
                      </Badge>
                      <Badge variant="outline" className="uppercase">
                        {account.purpose}
                      </Badge>
                    </div>
                    <CardDescription>{account.email}</CardDescription>
                  </div>

                  <div className="flex shrink-0 gap-2">
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
                    <Button size="sm" variant="outline" onClick={() => openEdit(account)}>
                      <Pencil />
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => setPendingDelete(account)}
                    >
                      <Trash2 />
                      Delete
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
                <span>Uses: {account.total_uses}</span>
                <span>Failures: {account.failure_count}</span>
                {account.last_used_at && (
                  <span>
                    Last used: {new Date(account.last_used_at).toLocaleString()}
                  </span>
                )}
                {account.notes && <span className="italic">{account.notes}</span>}
              </CardContent>
            </Card>
          ))}
        </div>
      </main>

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => (open ? setDialogOpen(true) : resetForm())}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editId ? 'Edit Account' : 'Add New Account'}</DialogTitle>
            <DialogDescription>
              {editId
                ? 'Update account info. Leave password empty to keep the current one.'
                : 'Add a new Facebook account for the worker pool.'}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="label">Label</Label>
              <Input
                id="label"
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="e.g. Account 1"
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
                required={!editId}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">
                Password
                {editId && (
                  <span className="text-muted-foreground text-xs font-normal">
                    (leave empty to keep current)
                  </span>
                )}
              </Label>
              <Input
                id="password"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required={!editId}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="purpose">Purpose</Label>
              <Select
                value={form.purpose}
                onValueChange={(value) => setForm({ ...form, purpose: value })}
              >
                <SelectTrigger id="purpose" className="w-full">
                  <SelectValue placeholder="Select purpose" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="both">Both (Scrape + Post)</SelectItem>
                  <SelectItem value="scrape">Scrape Only</SelectItem>
                  <SelectItem value="post">Post Only</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="notes">Notes</Label>
              <Input
                id="notes"
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                placeholder="Optional notes..."
              />
            </div>

            <DialogFooter className="sm:col-span-2">
              <Button type="button" variant="outline" onClick={resetForm}>
                Cancel
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting && <Loader2 className="animate-spin" />}
                {editId ? 'Save Changes' : 'Add Account'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={!!pendingDelete}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete account?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete{' '}
              <span className="text-foreground font-medium">
                {pendingDelete?.label}
              </span>
              . This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
              onClick={(e) => {
                e.preventDefault()
                if (pendingDelete) deleteMutation.mutate(pendingDelete.id)
              }}
            >
              {deleteMutation.isPending && <Loader2 className="animate-spin" />}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
