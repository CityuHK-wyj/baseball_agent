import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...values: ClassValue[]) { return twMerge(clsx(values)); }
const buttonVariants = cva('inline-flex items-center justify-center rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:pointer-events-none disabled:opacity-50', {
  variants: { variant: { default: 'bg-blue-700 text-white hover:bg-blue-800', outline: 'border border-slate-400 bg-transparent hover:bg-slate-100 dark:hover:bg-slate-800' } }, defaultVariants: { variant: 'default' },
});
export function Button({ className, variant, asChild = false, ...props }: React.ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button';
  return <Comp data-slot="button" className={cn(buttonVariants({ variant }), className)} {...props} />;
}
