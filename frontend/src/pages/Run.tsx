import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui";

type LogLine = { kind: "log" | "stage" | "error" | "done"; text: string };

export default function Run() {
  const qc = useQueryClient();
  const [lines, setLines] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [skipDecrypt, setSkipDecrypt] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await api.GET("/api/health")).data,
    refetchInterval: 10000,
  });

  const close = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setRunning(false);
    void qc.invalidateQueries({ queryKey: ["stats"] });
    void qc.invalidateQueries({ queryKey: ["runs"] });
    void qc.invalidateQueries({ queryKey: ["tasks"] });
    void qc.invalidateQueries({ queryKey: ["health"] });
  }, [qc]);

  useEffect(() => () => esRef.current?.close(), []);

  async function start() {
    setLines([]);
    const { error } = await api.POST("/api/run", {
      body: { skip_decrypt: skipDecrypt, dry_run: dryRun },
    });
    if (error) {
      setLines([{ kind: "error", text: (error as { detail?: string }).detail ?? "启动失败" }]);
      return;
    }
    setRunning(true);
    const es = new EventSource("/api/run/events");
    esRef.current = es;
    es.onmessage = (ev) => {
      const data = JSON.parse(ev.data) as Record<string, unknown>;
      const type = String(data.type ?? "log");
      let text = "";
      if (type === "stage") text = `── 阶段：${String(data.stage)} ──`;
      else if (type === "log") text = String(data.msg ?? "");
      else if (type === "error") text = `✖ ${String(data.msg ?? "")}`;
      else if (type === "done") text = `✔ 完成：${JSON.stringify(data.stats)}`;
      setLines((prev) => [...prev, { kind: type as LogLine["kind"], text }]);
      if (type === "done" || type === "error") close();
    };
    es.addEventListener("end", close);
    es.onerror = () => close();
  }

  async function decryptOnly() {
    setLines([{ kind: "log", text: "开始解密全库…" }]);
    const { error } = await api.POST("/api/decrypt");
    setLines((prev) => [
      ...prev,
      error
        ? { kind: "error", text: (error as { detail?: string }).detail ?? "解密失败" }
        : { kind: "log", text: "解密完成" },
    ]);
    void qc.invalidateQueries({ queryKey: ["health"] });
    void qc.invalidateQueries({ queryKey: ["sessions"] });
  }

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [lines]);

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <CardHeader>
          <CardTitle>运行控制</CardTitle>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-fg-muted">
              <input type="checkbox" className="accent-emerald-500" checked={skipDecrypt}
                onChange={(e) => setSkipDecrypt(e.target.checked)} />
              跳过解密（复用现有解密目录）
            </label>
            <label className="flex items-center gap-1.5 text-xs text-fg-muted">
              <input type="checkbox" className="accent-emerald-500" checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)} />
              dry-run（只组装请求包，不调模型）
            </label>
          </div>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button onClick={start} disabled={running || !health.data?.llm_configured && !dryRun}>
            {running ? "运行中…" : "开始增量运行"}
          </Button>
          <Button variant="outline" onClick={decryptOnly} disabled={running}>
            只解密数据库
          </Button>
          {running && <Spinner />}
          {!health.data?.llm_configured && (
            <Badge tone="high">模型未配置：去「设置」填 base_url / api_key</Badge>
          )}
          {dryRun && <Badge tone="info">dry-run 不需要模型</Badge>}
        </CardContent>
      </Card>

      <Card className="min-h-0 flex-1">
        <CardHeader>
          <CardTitle>实时日志（SSE）</CardTitle>
          {lines.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => setLines([])}>
              清空
            </Button>
          )}
        </CardHeader>
        <CardContent className="p-0">
          <div
            ref={boxRef}
            className="h-[calc(100vh-22rem)] overflow-auto rounded-b-xl bg-log-bg p-4 font-mono text-xs leading-6"
          >
            {lines.length === 0 && <div className="text-fg-faint">等待运行…</div>}
            {lines.map((l, i) => (
              <div
                key={i}
                className={
                  l.kind === "error"
                    ? "text-high"
                    : l.kind === "stage"
                      ? "mt-2 text-info"
                      : l.kind === "done"
                        ? "text-ok"
                        : "text-fg"
                }
              >
                {l.text}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
