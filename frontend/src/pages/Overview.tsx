import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { api, fmtTime } from "@/api/client";
import { Badge, Card, CardContent, CardHeader, CardTitle, Spinner, Td, Th } from "@/components/ui";
import { useTheme } from "@/components/theme-provider";
import { chartPalette } from "@/lib/chart-theme";

const PRIO_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低" };

export default function Overview() {
  const { resolvedTheme } = useTheme();
  const palette = chartPalette(resolvedTheme);
  const stats = useQuery({
    queryKey: ["stats"],
    queryFn: async () => (await api.GET("/api/stats")).data,
    refetchInterval: 20000,
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: async () => (await api.GET("/api/runs", { params: { query: { limit: 10 } } })).data,
  });

  if (stats.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-fg-muted">
        <Spinner /> 加载中…
      </div>
    );
  }
  const s = stats.data;
  const pending = s?.by_status?.pending ?? 0;
  const done = s?.by_status?.done ?? 0;
  const ignored = s?.by_status?.ignored ?? 0;
  const totalTokens = (s?.prompt_tokens ?? 0) + (s?.completion_tokens ?? 0);

  const pieOption = {
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { color: palette.axisLabel } },
    series: [
      {
        type: "pie",
        radius: ["45%", "70%"],
        itemStyle: { borderColor: palette.sliceBorder, borderWidth: 2 },
        label: { color: palette.label },
        data: Object.entries(s?.by_priority ?? {}).map(([k, v]) => ({
          name: PRIO_LABEL[k] ?? k,
          value: v,
        })),
      },
    ],
  };

  const barOption = {
    grid: { left: 40, right: 12, top: 16, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: (s?.recent_daily ?? []).map((d) => d.date.slice(5)),
      axisLabel: { color: palette.axisLabel },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: palette.axisLabel },
      splitLine: { lineStyle: { color: palette.splitLine } },
    },
    series: [
      {
        type: "bar",
        data: (s?.recent_daily ?? []).map((d) => d.n),
        itemStyle: { color: palette.bar, borderRadius: [4, 4, 0, 0] },
      },
    ],
  };

  const cards = [
    { label: "累计待办", value: s?.total_tasks ?? 0, tone: "low" as const },
    { label: "待办中", value: pending, tone: "medium" as const },
    { label: "已完成", value: done, tone: "ok" as const },
    { label: "已忽略", value: ignored, tone: "low" as const },
    { label: "Token 用量", value: totalTokens, tone: "info" as const },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {cards.map((c) => (
          <Card key={c.label}>
            <CardContent className="pt-4">
              <div className="text-xs text-fg-subtle">{c.label}</div>
              <div className="mt-1 text-2xl font-semibold text-fg-strong">{c.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>优先级分布</CardTitle>
          </CardHeader>
          <CardContent>
            {s?.by_priority && Object.keys(s.by_priority).length > 0 ? (
              <ReactECharts option={pieOption} style={{ height: 240 }} />
            ) : (
              <div className="py-16 text-center text-sm text-fg-subtle">还没有待办数据</div>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>近 14 天新增</CardTitle>
          </CardHeader>
          <CardContent>
            {s?.recent_daily && s.recent_daily.length > 0 ? (
              <ReactECharts option={barOption} style={{ height: 240 }} />
            ) : (
              <div className="py-16 text-center text-sm text-fg-subtle">还没有历史数据</div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>最近运行</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full">
            <thead>
              <tr>
                <Th>开始时间</Th>
                <Th>结果</Th>
                <Th>会话</Th>
                <Th>消息</Th>
                <Th>新增</Th>
                <Th>Token</Th>
              </tr>
            </thead>
            <tbody>
              {(runs.data?.items ?? []).map((r) => (
                <tr key={r.id} className="hover:bg-surface-2">
                  <Td>{fmtTime(r.started_at)}</Td>
                  <Td>
                    {r.ok === null ? (
                      <Badge tone="info">进行中</Badge>
                    ) : r.ok ? (
                      <Badge tone="ok">成功</Badge>
                    ) : (
                      <Badge tone="high">失败</Badge>
                    )}
                  </Td>
                  <Td>{String((r.stats as Record<string, unknown>)?.sessions ?? "-")}</Td>
                  <Td>{String((r.stats as Record<string, unknown>)?.messages ?? "-")}</Td>
                  <Td>{String((r.stats as Record<string, unknown>)?.inserted ?? "-")}</Td>
                  <Td>{r.prompt_tokens + r.completion_tokens}</Td>
                </tr>
              ))}
              {(runs.data?.items ?? []).length === 0 && (
                <tr>
                  <Td colSpan={6} className="py-8 text-center text-fg-subtle">
                    还没有运行记录，去「运行」页跑一次
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
