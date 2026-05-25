import type { ReactNode } from "react";

interface PageTitleProps {
  children: ReactNode;
  subtitle?: ReactNode;
  className?: string;
}

export function PageTitle({ children, subtitle, className }: PageTitleProps) {
  return (
    <div className={className}>
      <h1 className="font-display text-h1 font-extrabold text-foreground">
        {children}
      </h1>
      {subtitle ? (
        <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
      ) : null}
    </div>
  );
}
