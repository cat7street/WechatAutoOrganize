import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtTime } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge, Button, Card, CardContent, Spinner, Td, Th } from "@/components/ui";

type Task = components["schemas"]["TaskOut"];

const FILTERS = [
  { key: "", label: "全部" },
  { key: "pending", label: "待办中" },
  { key: "done", label: "已完成" },
  { key: "ignored", label: "已忽略" },
];

const PRIO_TONE: Record<string, "high" | "medium" | "low"> = {
  high: "high",
  medium: "medium",
  low: "low",
};

export default function Tasks() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("pending");

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
        <span className="ml-auto text-xs text-zinc-500">{items.length} 条</span>
      </div>

      {tasks.isLoading && (
        <div className="flex items-center gap-2 text-sm text-zinc-400">
          <Spinner /> 加载待办…
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          <table className="w-full">
            <thead>
              <tr>
                <Th className="w-20">优先级</Th>
                <Th>待办</Th>
                <Th className="w-28">责任人</Th>
                <Th className="w-28">截止</Th>
                <Th className="w-44">来源会话</Th>
                <Th className="w-40">创建</Th>
                <Th className="w-40">操作</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((t: Task) => (
                <tr key={t.id} className="align-top hover:bg-zinc-900/50">
                  <Td>
                    <Badge tone={PRIO_TONE[t.priority] ?? "low"}>{t.priority}</Badge>
                  </Td>
                  <Td>
                    <div className={t.status === "done" ? "text-zinc-500 line-through" : "text-zinc-100"}>
                      {t.title}
                    </div>
                    {t.quote && (
                      <div className="mt-1 max-w-xl text-[11px] text-zinc-500">
                        “{t.quote}”
                        <span className="ml-1 text-zinc-600">
                          msg:{t.msg_ids.join(",")}
                        </span>
                      </div>
                    )}
                  </Td>
                  <Td className="text-zinc-400">{t.owner || "-"}</Td>
                  <Td className="text-zinc-400">{t.due || "-"}</Td>
                  <Td className="truncate text-zinc-400">{t.session_display}</Td>
                  <Td className="text-xs text-zinc-500">{fmtTime(t.created_at)}</Td>
                  <Td>
                    <div className="flex gap-1">
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
                      {t.status !== "pending" && (
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
                  <Td colSpan={7} className="py-10 text-center text-zinc-500">
                    没有待办。去「运行」页跑一次增量，或调整白名单。
                  </Td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
