import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Drawer({
  open,
  title,
  subtitle,
  onClose,
  children,
  width = "max-w-3xl",
}: {
  open: boolean;
  title: string;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <div
        className={cn(
          "relative flex h-full w-full flex-col border-l border-line bg-surface shadow-2xl",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-fg-strong">{title}</div>
            {subtitle && <div className="mt-0.5 text-xs text-fg-subtle">{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-fg-muted hover:bg-surface-2 hover:text-fg-strong"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
      </div>
    </div>
  );
}
