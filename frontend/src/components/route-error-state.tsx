import { ArrowClockwise, ArrowLeft, WarningCircle } from "@phosphor-icons/react"
import { useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"

export interface RouteErrorStateProps {
  title: string
  message: string
  onRetry?: () => void
  backTo?: string
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
