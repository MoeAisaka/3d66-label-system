import { Component, Fragment, type ErrorInfo, type ReactNode } from "react"

import { UnknownMechanismSummary } from "./unknown-mechanism-summary"
import type { ConfigRevision } from "./types"

type Props = {
  detail: ConfigRevision | null
  onRetry?: () => void
  children: ReactNode
}

type State = { failed: boolean; resetKey: number }

export class MechanismEditorBoundary extends Component<Props, State> {
  state: State = { failed: false, resetKey: 0 }

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Mechanism editor render failed", error, info.componentStack)
  }

  private retry = () => {
    this.props.onRetry?.()
    this.setState(({ resetKey }) => ({ failed: false, resetKey: resetKey + 1 }))
  }

  render() {
    if (this.state.failed) {
      return (
        <div>
          <UnknownMechanismSummary
            detail={this.props.detail}
            reason="机制编辑插件渲染失败，已切换为只读安全降级。可刷新后重试，合同工件未被修改。"
          />
          <button type="button" className="mt-3 text-xs font-bold underline" onClick={this.retry}>重新加载编辑器</button>
        </div>
      )
    }
    return <Fragment key={this.state.resetKey}>{this.props.children}</Fragment>
  }
}
