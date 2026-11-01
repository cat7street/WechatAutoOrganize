import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge, Button, Card, CardContent, Spinner, Td, Th } from "@/components/ui";
import { Drawer } from "@/components/Drawer";
import { MessageList } from "@/components/Messages";

type Task = components["schemas"]["TaskOut"];
type Ctx = components["schemas"]["TaskContextOut"];

const FILTERS = [
  { key: "", label: "全部" },
  { key: "pending", label: "待办中" },
  { key: "done", label: "已完成" },
  { key: "ignored", label: "已忽略" },
  { key: "merged", label: "已合并" },
];

const PRIO_TONE: Record<string, "high" | "medium" | "low"> = {
  high: "high",
  medium: "medium",
  low: "low",
};

export default function Tasks() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("pending");
  const [ctxTask, setCtxTask] = useState<Task | null>(null);
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const [ctxLoading, setCtxLoading] = useState(false);
  const [params, setParams] = useSearchParams();

  const tasks = useQuery({
    queryKey: ["tasks", filter],
    queryFn: async () => {
      const { data } = await api.GET("/api/tasks", {
        params: { query: filter ? { status: filter, limit: 500 } : { limit: 500 } },
      });
      return data;
    },
  });

  const patch = useMutation({
    mutationFn: async ({ id, status }: { id: number; status: string }) => {
      await api.PATCH("/api/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body: { status },
      });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["tasks"] });
      await qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const merge = useMutation({
    mutationFn: async () => {
      const { data } = await api.POST("/api/merge");
      return data;
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["tasks"] });
      await qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  async function openContext(t: Task) {
    setCtxTask(t);
    setCtx(null);
    setCtxLoading(true);
    setParams({ task: String(t.id) }, { replace: true });
    const { data } = await api.GET("/api/tasks/{task_id}/context", {
      params: { path: { task_id: t.id }, query: { window: 6 } },
    });
    setCtx(data ?? null);
    setCtxLoading(false);
  }

  function closeContext() {
    setCtxTask(null);
    setParams({}, { replace: true });
  }

  // 深链：/tasks?task=123 直接展开某条待办的上下文
  useEffect(() => {
    const id = params.get("task");
    if (!id) return;
    void (async () => {
      const { data } = await api.GET("/api/tasks/{task_id}", {
        params: { path: { task_id: Number(id) } },
      });
      if (data) await openContext(data as Task);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const items = tasks.data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        {FILTERS.map((f) => (
          <Button
            key={f.key}
            size="sm"
            variant={filter === f.key ? "default" : "outline"}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </Button>
        ))}
        <span className="ml-auto text-xs text-fg-subtle">{items.length} 条</span>
        <Button size="sm" variant="outline" onClick={() => merge.mutate()}
          disabled={merge.isPending}>
          {merge.isPending ? "合并中…" : "AI 合并去重"}
        </Button>
        {merge.data && (
          <span className="text-[11px] text-ok">
            {merge.data.groups} 组 / 吸收 {merge.data.merged_away} / 提级 {merge.data.lifted}
          </span>
        )}
      </div>

      {tasks.isLoading && (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> 加载待办…
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          <table className="w-full">
            <thead>
              <tr>
                <Th className="w-20">优先级</Th>
                <Th className="w-14">提及</Th>
                <Th>待办</Th>
                <Th className="w-28">责任人</Th>
                <Th className="w-28">截止</Th>
                <Th className="w-32">来源会话</Th>
                <Th className="w-56">操作</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((t: Task) => (
                <tr key={t.id} className="align-top hover:bg-surface-2">
                  <Td>
                    <Badge tone={PRIO_TONE[t.priority] ?? "low"}>{t.priority}</Badge>
                  </Td>
                  <Td className="tabular-nums text-fg-muted">{t.mentions ?? 1}</Td>
                  <Td>
                    <div
                      className={
                        t.status === "done"
                          ? "text-fg-subtle line-through"
                          : "font-medium text-fg-strong"
                      }
                    >
                      {t.title}
                    </div>
                    {t.quote && (
                      <div className="mt-1 max-w-xl text-[11px] text-fg-muted">
                        “{t.quote}”
                        <span className="ml-1 text-fg-faint">msg:{t.msg_ids.join(",")}</span>
                      </div>
                    )}
                  </Td>
                  <Td className="text-fg-muted">{t.owner || "-"}</Td>
                  <Td className="text-fg-muted">{t.due || "-"}</Td>
                  <Td className="truncate text-fg-muted">{t.session_display}</Td>
                  <Td>
                    <div className="flex flex-nowrap gap-1">
                      <Button size="sm" variant="outline" onClick={() => openContext(t)}>
                        看上下文
                      </Button>
                      {t.status !== "done" && (
                        <Button size="sm" variant="secondary"
                          onClick={() => patch.mutate({ id: t.id, status: "done" })}>
                          完成
                        </Button>
                      )}
                      {t.status !== "ignored" && (
                        <Button size="sm" variant="ghost"
                          onClick={() => patch.mutate({ id: t.id, status: "ignored" })}>
                          忽略
                        </Button>
                      )}
                      {t.status !== "pending" && t.status !== "merged" && (
                        <Button size="sm" variant="ghost"
                          onClick={() => patch.mutate({ id: t.id, status: "pending" })}>
                          恢复
                        </Button>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
              {items.length === 0 && !tasks.isLoading && (
                <tr>
                  <Td colSpan={7} className="py-10 text-center text-fg-subtle">
                    没有待办。去「运行」页跑一次增量，或调整白名单。
                  </Td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Drawer
        open={ctxTask !== null}
        title={ctxTask ? ctxTask.title : ""}
        subtitle={
          ctxTask ? (
            <span>
              {ctxTask.session_display} · 提及 {ctxTask.mentions ?? 1} 次 · msg:
              {ctxTask.msg_ids.join(",")}
            </span>
          ) : null
        }
        onClose={closeContext}
      >
        {ctxLoading && (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> 读取原文…
          </div>
        )}
        {ctx?.missing && <div className="mb-2 text-sm text-medium">{ctx.missing}</div>}
        {ctx && <MessageList messages={ctx.messages} />}
      </Drawer>
    </div>
  );
}
