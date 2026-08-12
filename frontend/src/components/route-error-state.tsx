import { ArrowClockwise, ArrowLeft, WarningCircle } from "@phosphor-icons/react"
import { Component, Fragment, type ErrorInfo, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"

export interface RouteErrorStateProps {
  title: string
  message: string
  onRetry?: () => void
  backTo?: string
}

export interface RouteErrorBoundaryProps extends RouteErrorStateProps {
  children: ReactNode
}

interface RouteErrorBoundaryState {
  failed: boolean
  resetKey: number
}

export function RouteErrorState({ title, message, onRetry, backTo }: RouteErrorStateProps) {
  const navigate = useNavigate()
  return (
    <div className="mx-auto max-w-[720px] px-5 py-12 md:px-8 lg:px-10">
      <div className="border-y border-[var(--line-strong)] bg-white px-6 py-8">
        <WarningCircle className="text-[#a85a0a]" size={32} weight="fill" aria-hidden="true" />
        <h1 className="font-editorial mt-4 text-2xl font-bold">{title}</h1>
        <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{message}</p>
        <div className="mt-6 flex flex-wrap gap-2">
          {onRetry && <Button onClick={onRetry}><ArrowClockwise />重新加载</Button>}
          {backTo && <Button variant="secondary" onClick={() => navigate(backTo)}><ArrowLeft />返回</Button>}
        </div>
      </div>
    </div>
  )
}

export class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { failed: false, resetKey: 0 }

  static getDerivedStateFromError(): Partial<RouteErrorBoundaryState> {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Route render failed", error, info.componentStack)
  }

  private retry = () => {
    this.props.onRetry?.()
    this.setState(({ resetKey }) => ({ failed: false, resetKey: resetKey + 1 }))
  }

  render() {
    const { children, title, message, backTo } = this.props
    if (this.state.failed) {
      return <RouteErrorState title={title} message={message} onRetry={this.retry} backTo={backTo} />
    }
    return <Fragment key={this.state.resetKey}>{children}</Fragment>
  }
}
