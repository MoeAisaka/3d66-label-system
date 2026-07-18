import * as React from "react"

import { cn } from "@/lib/utils"

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "min-h-32 w-full resize-y rounded-[4px] border border-[var(--line-strong)] bg-white px-3 py-2.5 text-base leading-6 text-foreground transition-[border-color,box-shadow] placeholder:text-[#70766d] focus-visible:border-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 disabled:bg-[#f1f3ef] md:text-sm",
        className,
      )}
      {...props}
    />
  ),
)
Textarea.displayName = "Textarea"
