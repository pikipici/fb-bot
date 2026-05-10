# shadcn/ui Migration Plan — Dashboard

Goal: pindahin semua UI dashboard (`dashboard/`) ke primitives shadcn/ui. Design system jadi konsisten, dark mode default, base color netral (neutral).

## Scope

- Refactor semua komponen existing (3 pages: Login, ReviewQueue, FBAccounts)
- Style `new-york`, base color `neutral`
- Dark mode via class di `<html>`, default `system`
- Gak install via CLI shadcn — commit file langsung (lokal = code + git only, runtime di server)

## State Awal

- React 19.2 + Vite 8 + TS 6
- Tailwind v4 (`@tailwindcss/vite`, `@import "tailwindcss"`)
- Utilities Tailwind raw (bg-gray-900 dll), no design tokens
- Belum ada alias `@/*`, `components.json`, CVA, Radix

## Tahap

### 1. Dependencies (`package.json`)

Tambah:
- `@radix-ui/react-slot`
- `@radix-ui/react-dialog`
- `@radix-ui/react-dropdown-menu`
- `@radix-ui/react-label`
- `@radix-ui/react-select`
- `@radix-ui/react-separator`
- `class-variance-authority`
- `clsx`
- `tailwind-merge`
- `lucide-react`
- `tw-animate-css`
- `sonner`

### 2. Path alias `@/*`

- `tsconfig.json` + `tsconfig.app.json`: `paths: { "@/*": ["./src/*"] }`, `baseUrl: "."`
- `vite.config.ts`: `resolve.alias: { "@": path.resolve(__dirname, "./src") }`

### 3. `components.json`

```json
{
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/index.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

### 4. `src/lib/utils.ts`

`cn()` helper = `twMerge(clsx(...))`.

### 5. `src/index.css`

- `@import "tailwindcss"`
- `@import "tw-animate-css"`
- `@custom-variant dark (&:is(.dark *))`
- `@theme inline` map CSS vars ke Tailwind tokens (`--color-background`, `--color-foreground`, dst.)
- `:root` + `.dark` pake palette neutral (oklch shadcn default new-york)
- Radius `--radius: 0.625rem`
- Chart colors + sidebar tokens (shadcn convention)
- Body base style (`bg-background text-foreground`)

### 6. Theme Provider + Toggle

- `src/components/theme-provider.tsx` — custom (tanpa `next-themes`), support `light`/`dark`/`system`, persist ke `localStorage`, apply `.dark` ke `<html>`.
- `src/components/theme-toggle.tsx` — dropdown-menu + lucide `Sun`/`Moon`.

### 7. UI Primitives (`src/components/ui/`)

Semua versi shadcn new-york:
- `button.tsx`
- `input.tsx`
- `label.tsx`
- `card.tsx`
- `badge.tsx`
- `select.tsx`
- `dialog.tsx`
- `dropdown-menu.tsx`
- `alert.tsx`
- `separator.tsx`
- `sonner.tsx`
- `table.tsx`

### 8. Refactor Pages

**Login.tsx**
- Full-page flex center, bg-background
- `<Card>` wrapper, judul di `CardHeader`
- `<Label>` + `<Input>` buat username/password
- `<Button>` submit dengan loading state
- Error pake `<Alert variant="destructive">`

**ReviewQueue.tsx**
- Header pake `<Separator>` + `<Button variant="ghost">` nav
- `<Badge>` buat source_type & role
- Tiap draft = `<Card>`, action pakai `<Button>` (approve=default, reject=destructive)
- Empty state tetep `<Card>` muted
- Theme toggle di header kanan

**FBAccounts.tsx**
- Same header pattern
- Form pake `<Dialog>` (ganti inline form) — trigger pake `<Button>`
- Field: `<Label>` + `<Input>` / `<Select>` (purpose)
- Delete confirm pake `<AlertDialog>` (ganti native `confirm`)
- List pake `<Card>`, status pake `<Badge>` variant sesuai status
- Toast sukses/error pake `<Toaster>` dari `sonner`

### 9. Cleanup

- Hapus `src/App.css` (unused template)
- Hapus `src/assets/hero.png`, `src/assets/react.svg`, `src/assets/vite.svg`
- Hapus `main.tsx` import `App.css` kalau ada

### 10. Verifikasi

Lokal (cuma tipe-check tanpa install):
- Visual review diff
- Tsc via `tsc -b --noEmit` (jalanin di server, bukan lokal)

Server (via SSH setelah push):
- `cd /home/ubuntu/fb-bot/dashboard && npm install`
- `npm run build` → cek dist size, output ada `index.html`
- Nginx reload (static udah kebaca dari dist)
- Smoke: `fbtun` dari lokal → buka `http://localhost:8080`, test login + navigasi + dark/light toggle

### 11. Commit & Deploy

- Branch `feat/shadcn-ui-migration`
- Commits atomic:
  1. `chore: add shadcn deps + path alias + components.json`
  2. `feat: add shadcn theme tokens + cn util + theme provider`
  3. `feat: add shadcn ui primitives (button, input, card, ...)`
  4. `refactor: migrate Login to shadcn`
  5. `refactor: migrate ReviewQueue to shadcn`
  6. `refactor: migrate FBAccounts to shadcn + dialogs`
  7. `chore: remove vite template css/assets`
- Push → deploy via standard fb-bot flow
- Update `development-behavior.md` activity log

## Acceptance

- Semua page pakai primitives shadcn, no direct `bg-gray-*` utility buat surface
- Dark/Light toggle jalan + persist
- Build sukses, deployed, `/api/v1/health` 200, login + review + accounts page functional
- No unused deps, no template leftover
