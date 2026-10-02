import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  message?: string;
  children?: ReactNode;
}

export function EmptyState({ title, message, children }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <Inbox aria-hidden="true" className="h-10 w-10 text-slate-300" />
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      {message && <p className="max-w-md text-sm text-slate-500">{message}</p>}
      {children}
    </div>
  );
}
