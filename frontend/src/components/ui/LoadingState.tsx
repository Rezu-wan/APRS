import { Loader2 } from "lucide-react";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-slate-500" role="status">
      <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin" />
      <span className="text-sm">{label}</span>
      <span className="sr-only">Loading</span>
    </div>
  );
}
