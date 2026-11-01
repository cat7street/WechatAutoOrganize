import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { api, fmtTime } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge, Button, Card, CardContent, Input, Spinner } from "@/components/ui";

type Session = components["schemas"]["SessionOut"];

export default function Sessions() {
  const qc = useQueryClient();
  const [keyword, setKeyword] = useState("");
  const [draft, setDraft] = useState<string[] | null>(null);
  const parentRef = useRef<HTMLDivElement>(null);

  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/sessions");
      if (error) throw new Error((error as { detail?: string }).detail ?? "拉取失败");
      return data;
    },
  });

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/settings")).data,
  });

  const whitelist = draft ?? settings.data?.whitelist ?? [];

  const save = useMutation({
    mutationFn: async (next: string[]) => {
      const { error } = await api.POST("/api/settings/whitelist", { body: { whitelist: next } });
      if (error) throw new Error("保存失败");
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["sessions"] });
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["health"] });
      setDraft(null);
    },
  });

  const rows = useMemo(() => {
    const items = sessions.data?.items ?? [];
    const kw = keyword.trim().toLowerCase();
    const filtered = kw
      ? items.filter(
          (s) =>
            s.display.toLowerCase().includes(kw) || s.username.toLowerCase().includes(kw),
        )
      : items;
    // 与后端同一条规则：白名单里任一关键词命中 username/display 就算纳入
    const marks = whitelist.map((w) => w.toLowerCase());
    return filtered.map((s) => {
      const hit = marks.some(
        (w) => s.username.toLowerCase().includes(w) || s.display.toLowerCase().includes(w),
      );
      return { ...s, selected: hit };
    });
  }, [sessions.data, keyword, whitelist]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 12,
  });

  function toggle(s: Session) {
    const marks = whitelist.map((w) => w.toLowerCase());
    const selected = marks.some(
      (w) => s.username.toLowerCase().includes(w) || s.display.toLowerCase().includes(w),
    );
    let next: string[];
    if (selected) {
      next = whitelist.filter((w) => {
        const lw = w.toLowerCase();
        return !(s.username.toLowerCase().includes(lw) || s.display.toLowerCase().includes(lw));
      });
    } else {
      next = [...whitelist, s.username];
    }
    setDraft(next);
  }

  if (sessions.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-fg-muted">
        <Spinner /> 读取会话…
      </div>
    );
  }
  if (sessions.error) {
    return (
      <Card>
        <CardContent className="py-8 text-sm text-medium">
          {(sessions.error as Error).message}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="搜索备注名 / wxid / 群 ID"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          className="max-w-sm"
        />
        <div className="text-xs text-fg-subtle">
          共 {sessions.data?.total ?? 0} 个会话，已纳入 {whitelist.length} 个
        </div>
        <div className="ml-auto flex gap-2">
          {draft && (
            <Button variant="ghost" onClick={() => setDraft(null)}>
              撤销
            </Button>
          )}
          <Button
            disabled={!draft || save.isPending}
            onClick={() => draft && save.mutate(draft)}
          >
            {save.isPending ? "保存中…" : "保存白名单"}
          </Button>
        </div>
      </div>

      <Card className="min-h-0 flex-1">
        <div className="grid grid-cols-[48px_1fr_120px_120px_150px] border-b border-line px-3 py-2 text-xs text-fg-muted">
          <span>纳入</span>
          <span>会话</span>
          <span className="text-right">消息数</span>
          <span className="text-right">未读</span>
          <span className="text-right">最后消息</span>
        </div>
        <div ref={parentRef} className="h-[calc(100%-2.25rem)] overflow-auto">
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const s = rows[vi.index];
              return (
                <div
                  key={s.username}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    right: 0,
                    height: vi.size,
                    transform: `translateY(${vi.start}px)`,
                  }}
                  className="grid grid-cols-[48px_1fr_120px_120px_150px] items-center border-b border-line/50 px-3 text-sm hover:bg-surface-2"
                >
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-emerald-500"
                    checked={s.selected}
                    onChange={() => toggle(s)}
                  />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-fg-strong">{s.display}</span>
                      {s.is_chatroom && <Badge tone="info">群</Badge>}
                    </div>
                    <div className="truncate text-[11px] text-fg-subtle">{s.username}</div>
                  </div>
                  <span className="text-right tabular-nums text-fg">{s.msg_count}</span>
                  <span className="text-right tabular-nums text-fg-muted">{s.unread}</span>
                  <span className="text-right text-xs text-fg-subtle">{fmtTime(s.last_ts)}</span>
                </div>
              );
            })}
          </div>
        </div>
      </Card>
    </div>
  );
}
