import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex min-h-11 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-[4px] px-4 text-sm font-bold transition-[background-color,color,border-color,transform] duration-[180ms] ease-out disabled:pointer-events-none disabled:opacity-45 active:translate-y-px [&_svg]:size-[18px] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-primary text-primary-foreground hover:bg-[#bfdc3f]",
        secondary: "border border-[var(--line-strong)] bg-white text-foreground hover:border-[#bfc6ba] hover:bg-[#f8f9f6]",
        ghost: "bg-transparent text-foreground hover:bg-[#eef1eb]",
        danger: "bg-[#b7362e] text-white hover:bg-[#9f2d27]",
      },
      size: {
        default: "h-11",
        sm: "h-9 min-h-9 px-3 text-[0.8125rem]",
        icon: "size-11 px-0",
      },
    },
    defaultVariants: { variant: "primary", size: "default" },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  },
)
Button.displayName = "Button"

export { buttonVariants }
