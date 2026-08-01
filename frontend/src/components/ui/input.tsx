import * as React from "react"

import { cn } from "@/lib/utils"

export const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-11 min-w-0 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-base text-foreground transition-[border-color,box-shadow] placeholder:text-[#70766d] hover:border-[#bfc6ba] focus-visible:border-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:bg-[#f1f3ef] disabled:opacity-70 md:text-sm",
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = "Input"
