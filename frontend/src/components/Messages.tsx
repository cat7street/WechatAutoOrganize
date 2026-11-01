import type { components } from "@/api/schema";
import { cn } from "@/lib/utils";

type ViewMessage = components["schemas"]["ViewMessage"];

function fmt(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function MessageList({ messages }: { messages: ViewMessage[] }) {
  if (messages.length === 0) {
    return <div className="py-10 text-center text-sm text-fg-subtle">没有消息</div>;
  }
  return (
    <div className="flex flex-col gap-2">
      {messages.map((m) => (
        <div
          key={m.local_id}
          className={cn(
            "rounded-lg border px-3 py-2 text-sm",
            m.is_source ? "border-ok/50 bg-ok/5" : "border-line bg-surface-2",
          )}
        >
          <div className="mb-1 flex items-center gap-2 text-[11px] text-fg-subtle">
            <span className="font-mono">#{m.local_id}</span>
            <span>{fmt(m.ts)}</span>
            <span className="text-fg-muted">{m.sender_display}</span>
            {m.type !== "1" && <span className="text-fg-faint">type {m.type}</span>}
            {m.is_source && <span className="font-medium text-ok">待办依据</span>}
          </div>
          {m.text && <div className="whitespace-pre-wrap break-words text-fg">{m.text}</div>}
          {m.attachments.map((a, i) => (
            <div key={i} className="mt-1 rounded border border-line bg-surface p-2">
              <div className="text-[11px] text-info">
                {a.label}
                {a.has_image && <span className="ml-1 text-fg-faint">（图片，未展开缩略图）</span>}
              </div>
              {a.text && (
                <div className="mt-1 whitespace-pre-wrap break-words text-xs text-fg-muted">
                  {a.text}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
