import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw, Home } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'

interface Props {
  children: ReactNode
  /** Optional reset callback (clear route state, invalidate queries, etc). */
  onReset?: () => void
  /** Scope label shown in fallback, e.g. 'halaman Trending'. */
  scope?: string
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
}

/**
 * Generic React Error Boundary with a friendly fallback.
 *
 * Intended usage:
 *   <ErrorBoundary scope="halaman History">
 *     <History />
 *   </ErrorBoundary>
 *
 * Renders a Card with the error message + component stack (dev only) and
 * two recovery buttons — "Refresh halaman" (window reload) and "Ke
 * Trending" (router navigate). Production shows only the message, not
 * the stack.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, errorInfo: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Always log so devs can debug in console even in production.
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, errorInfo)
    this.setState({ errorInfo })
  }

  handleReset = (): void => {
    this.props.onReset?.()
    this.setState({ hasError: false, error: null, errorInfo: null })
  }

  handleReload = (): void => {
    window.location.reload()
  }

  handleGoHome = (): void => {
    window.location.href = '/'
  }

  render(): ReactNode {
    if (!this.state.hasError || !this.state.error) {
      return this.props.children
    }

    const isDev = import.meta.env.DEV
    const { error, errorInfo } = this.state
    const scope = this.props.scope || 'aplikasi'

    return (
      <div className="bg-background min-h-screen p-4 sm:p-8">
        <div className="mx-auto max-w-2xl">
          <Card className="border-destructive/50">
            <CardHeader className="space-y-1 pb-3">
              <div className="text-destructive flex items-center gap-2">
                <AlertTriangle className="h-5 w-5" />
                <span className="font-semibold">
                  Ada error di {scope}
                </span>
              </div>
              <p className="text-muted-foreground text-sm">
                Sesuatu jebol waktu render UI. Coba refresh atau balik ke
                Trending. Kalau kejadian lagi, cek console browser buat
                detail.
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="bg-muted/40 rounded-md border p-3">
                <div className="font-mono text-sm break-words">
                  {error.name}: {error.message}
                </div>
              </div>

              {isDev && errorInfo?.componentStack ? (
                <details className="bg-muted/20 rounded-md border p-3">
                  <summary className="text-muted-foreground cursor-pointer text-xs">
                    Component stack (dev only)
                  </summary>
                  <pre className="text-muted-foreground mt-2 text-[10px] whitespace-pre-wrap break-words">
                    {errorInfo.componentStack}
                  </pre>
                </details>
              ) : null}

              {isDev && error.stack ? (
                <details className="bg-muted/20 rounded-md border p-3">
                  <summary className="text-muted-foreground cursor-pointer text-xs">
                    Stack trace (dev only)
                  </summary>
                  <pre className="text-muted-foreground mt-2 text-[10px] whitespace-pre-wrap break-words">
                    {error.stack}
                  </pre>
                </details>
              ) : null}

              <div className="flex flex-wrap gap-2 pt-1">
                <Button size="sm" onClick={this.handleReload}>
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Refresh halaman
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={this.handleGoHome}
                >
                  <Home className="mr-1.5 h-3.5 w-3.5" />
                  Ke Trending
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={this.handleReset}
                >
                  Coba render ulang
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }
}
