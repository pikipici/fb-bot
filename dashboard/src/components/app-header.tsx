import { useNavigate, useLocation } from 'react-router-dom'
import { LogOut } from 'lucide-react'

import { useAuthStore } from '@/store/authStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { ThemeToggle } from '@/components/theme-toggle'
import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  adminOnly?: boolean
}

const navItems: NavItem[] = [
  { to: '/', label: 'Review' },
  { to: '/sources', label: 'Sumber', adminOnly: true },
  { to: '/accounts', label: 'Accounts', adminOnly: true },
]

export function AppHeader() {
  const { username, role, logout } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <header className="bg-background/95 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-40 border-b backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-4">
          <span className="text-sm font-semibold tracking-tight">
            FB Engagement Assistant
          </span>
          <Separator orientation="vertical" className="h-5" />
          <nav className="flex items-center gap-1">
            {navItems
              .filter((item) => !item.adminOnly || role === 'admin')
              .map((item) => {
                const active = location.pathname === item.to
                return (
                  <Button
                    key={item.to}
                    variant={active ? 'secondary' : 'ghost'}
                    size="sm"
                    onClick={() => navigate(item.to)}
                    className={cn(active && 'font-medium')}
                  >
                    {item.label}
                  </Button>
                )
              })}
          </nav>
        </div>

        <div className="flex items-center gap-2">
          <div className="text-muted-foreground hidden items-center gap-2 text-xs sm:flex">
            <span>{username}</span>
            {role && (
              <Badge variant="outline" className="text-[10px] uppercase">
                {role}
              </Badge>
            )}
          </div>
          <ThemeToggle />
          <Button variant="ghost" size="icon" onClick={handleLogout} aria-label="Logout">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </header>
  )
}
