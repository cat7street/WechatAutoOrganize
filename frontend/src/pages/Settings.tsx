import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "@/components/ui";

export default function Settings() {
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/settings")).data,
  });

  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [format, setFormat] = useState("json_object");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!settings.data) return;
    setBaseUrl(settings.data.base_url);
    setModel(settings.data.model);
    setFormat(settings.data.response_format);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = { base_url: baseUrl, model, response_format: format };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const { error } = await api.POST("/api/settings/llm", { body });
      if (error) throw new Error("保存失败");
    },
    onSuccess: async () => {
      setApiKey("");
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["health"] });
    },
  });

  return (
    <div className="grid max-w-3xl gap-3">
      <Card>
        <CardHeader>
          <CardTitle>模型服务（OpenAI 兼容）</CardTitle>
          <Badge tone={settings.data?.api_key_masked ? "ok" : "high"}>
            {settings.data?.api_key_masked ? `已配置 ${settings.data.api_key_masked}` : "未配置密钥"}
          </Badge>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div>
            <Label>Base URL</Label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.deepseek.com/v1"
            />
            <p className="mt-1 text-[11px] text-zinc-500">
              任何 OpenAI 兼容端点都行，例如 https://api.openai.com/v1、本地 http://127.0.0.1:11434/v1
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>模型名</Label>
              <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
            </div>
            <div>
              <Label>结构化输出模式</Label>
              <select
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm"
              >
                <option value="json_object">json_object（兼容面最广）</option>
                <option value="json_schema">json_schema（端点需支持）</option>
              </select>
            </div>
          </div>
          <div>
            <Label>API Key（只写不读，保存后仅显示掩码）</Label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={settings.data?.api_key_masked || "sk-..."}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? "保存中…" : "保存"}
            </Button>
            {saved && <span className="text-xs text-emerald-400">已保存</span>}
            {save.error && <span className="text-xs text-red-400">{(save.error as Error).message}</span>}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>运行参数</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <Label>首次回看窗口</Label>
            <div className="text-zinc-200">{settings.data?.window_hours ?? "-"} 小时</div>
          </div>
          <div>
            <Label>单请求字符预算</Label>
            <div className="text-zinc-200">{settings.data?.max_chars_per_request ?? "-"}</div>
          </div>
          <div>
            <Label>数据库目录</Label>
            <div className="break-all text-xs text-zinc-400">{settings.data?.db_dir || "-"}</div>
          </div>
          <div>
            <Label>解密产物目录</Label>
            <div className="break-all text-xs text-zinc-400">{settings.data?.work_dir || "-"}</div>
          </div>
          <div>
            <Label>Markdown 输出目录</Label>
            <div className="break-all text-xs text-zinc-400">{settings.data?.out_dir || "-"}</div>
          </div>
          <div>
            <Label>纳入的消息类型</Label>
            <div className="text-zinc-400">{settings.data?.text_types?.join(", ") || "-"}</div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
