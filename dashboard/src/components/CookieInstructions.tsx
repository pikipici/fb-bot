import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

import { Button } from '@/components/ui/button'

export function CookieInstructions() {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-md border border-border/60 bg-muted/30 text-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 font-medium"
      >
        <span>Gimana cara dapet cookie Facebook?</span>
        {open ? (
          <ChevronUp className="size-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="size-4 text-muted-foreground" />
        )}
      </button>
      {open && (
        <div className="space-y-3 border-t border-border/60 px-4 py-4 text-muted-foreground">
          <ol className="list-decimal space-y-2 pl-5">
            <li>
              Install extension{' '}
              <strong className="text-foreground">Cookie-Editor</strong> di
              Chrome, Firefox, atau Edge lu. Cari di webstore browser lu.
            </li>
            <li>
              Buka{' '}
              <a
                href="https://www.facebook.com/"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                facebook.com
              </a>
              , login kayak biasa kalau belum login.
            </li>
            <li>
              Klik icon Cookie-Editor di toolbar browser lu. Pastiin domain-nya
              di extension adalah <code>.facebook.com</code>.
            </li>
            <li>
              Pilih tab <strong className="text-foreground">Export</strong>{' '}
              (biasanya tombol di pojok bawah).
            </li>
            <li>
              Pilih format{' '}
              <strong className="text-foreground">Header String</strong> (bukan
              JSON). Cookie bakal otomatis ke-copy ke clipboard.
            </li>
            <li>
              Paste hasilnya di kolom di bawah, terus klik{' '}
              <strong className="text-foreground">Preview Akun</strong> buat
              verify.
            </li>
          </ol>
          <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-900 dark:text-amber-200">
            Cookie ini setara sama password — siapapun yang pegang cookie lu
            bisa login sebagai lu. Bot ini nyimpen cookie lu encrypted, tapi
            jangan share cookie string-nya ke siapapun.
          </p>
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Tutup
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
