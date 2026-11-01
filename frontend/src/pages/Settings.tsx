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
  const [media, setMedia] = useState({ images: true, files: true, voice: true, chars: 4000 });

  const schedule = useQuery({
    queryKey: ["schedule"],
    queryFn: async () => (await api.GET("/api/schedule")).data,
    refetchInterval: 15000,
  });
  const saveSchedule = useMutation({
    mutationFn: async (body: { enabled?: boolean; interval_minutes?: number }) => {
      const { error } = await api.POST("/api/schedule", { body });
      if (error) throw new Error((error as { detail?: string }).detail ?? "保存失败");
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["schedule"] });
    },
  });

  useEffect(() => {
    if (!settings.data) return;
    setBaseUrl(settings.data.base_url);
    setModel(settings.data.model);
    setFormat(settings.data.response_format);
    setMedia({
      images: settings.data.media_images,
      files: settings.data.media_files,
      voice: settings.data.media_voice,
      chars: settings.data.max_file_chars,
    });
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

  const saveMedia = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/settings/media", {
        body: {
          images: media.images,
          files: media.files,
          voice: media.voice,
          max_file_chars: media.chars,
        },
      });
      if (error) throw new Error("保存失败");
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["settings"] });
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
            <p className="mt-1 text-[11px] text-fg-subtle">
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
                className="h-9 w-full rounded-md border border-line-strong bg-field px-2 text-sm text-fg"
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
            {saved && <span className="text-xs text-ok">已保存</span>}
            {save.error && <span className="text-xs text-high">{(save.error as Error).message}</span>}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>媒体消息</CardTitle>
          <Badge tone="info">图片走视觉 · 文件抽正文</Badge>
        </CardHeader>
        <CardContent className="grid gap-3">
          <label className="flex items-center gap-2 text-sm text-fg">
            <input
              type="checkbox"
              className="accent-emerald-500"
              checked={media.images}
              onChange={(e) => setMedia({ ...media, images: e.target.checked })}
            />
            图片：解 .dat 后作为图像喂给多模态模型（原图为 wxgf/HEVC，实际用的是可解码的变体）
          </label>
          <label className="flex items-center gap-2 text-sm text-fg">
            <input
              type="checkbox"
              className="accent-emerald-500"
              checked={media.files}
              onChange={(e) => setMedia({ ...media, files: e.target.checked })}
            />
            文件：本地抽正文（xlsx/docx/pdf/txt/csv/md）后当文本
          </label>
          <label className="flex items-center gap-2 text-sm text-fg">
            <input
              type="checkbox"
              className="accent-emerald-500"
              checked={media.voice}
              onChange={(e) => setMedia({ ...media, voice: e.target.checked })}
            />
            语音：仅元信息（时长）；本机无音频文件时不编造内容
          </label>
          <div className="max-w-xs">
            <Label>单个文件最多抽取字符数</Label>
            <Input
              type="number"
              value={media.chars}
              onChange={(e) => setMedia({ ...media, chars: Number(e.target.value) })}
            />
          </div>
          <div>
            <Button onClick={() => saveMedia.mutate()} disabled={saveMedia.isPending}>
              {saveMedia.isPending ? "保存中…" : "保存媒体设置"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>定时运行</CardTitle>
          <Badge tone={schedule.data?.enabled ? "ok" : "low"}>
            {schedule.data?.enabled ? "已开启" : "已关闭"}
          </Badge>
        </CardHeader>
        <CardContent className="grid gap-3">
          <p className="text-xs text-fg-muted">
            自动跑一轮：解密 → WAL 合并 → <b>48 小时全量对账</b>（把完整对话 + 当前待办一起交给模型，
            让它整体更新）→ 合并去重 → 出当天 Markdown。定时一律用全量对账，避免只看增量导致判断失真。
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-40">
              <Label>间隔（分钟）</Label>
              <Input
                type="number"
                min={5}
                value={schedule.data?.interval_minutes ?? 60}
                onChange={(e) => saveSchedule.mutate({ interval_minutes: Number(e.target.value) })}
              />
            </div>
            <Button
              onClick={() => saveSchedule.mutate({ enabled: !schedule.data?.enabled })}
              disabled={saveSchedule.isPending}
            >
              {schedule.data?.enabled ? "关闭定时" : "开启定时"}
            </Button>
            {saveSchedule.error && (
              <span className="text-xs text-high">{(saveSchedule.error as Error).message}</span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs text-fg-muted md:grid-cols-4">
            <div>
              下次运行
              <div className="text-fg">
                {schedule.data?.next_at
                  ? new Date(schedule.data.next_at * 1000).toLocaleString("zh-CN", { hour12: false })
                  : "-"}
              </div>
            </div>
            <div>
              上次运行
              <div className="text-fg">
                {schedule.data?.last_at
                  ? new Date(schedule.data.last_at * 1000).toLocaleString("zh-CN", { hour12: false })
                  : "-"}
              </div>
            </div>
            <div>
              上次结果
              <div className="text-fg">{schedule.data?.last_result || "-"}</div>
            </div>
            <div>
              已跑/跳过
              <div className="text-fg">
                {schedule.data?.runs ?? 0} / {schedule.data?.skipped ?? 0}
              </div>
            </div>
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
            <div className="text-fg-strong">{settings.data?.window_hours ?? "-"} 小时</div>
          </div>
          <div>
            <Label>单请求字符预算</Label>
            <div className="text-fg-strong">{settings.data?.max_chars_per_request ?? "-"}</div>
          </div>
          <div>
            <Label>数据库目录</Label>
            <div className="break-all text-xs text-fg-muted">{settings.data?.db_dir || "-"}</div>
          </div>
          <div>
            <Label>解密产物目录</Label>
            <div className="break-all text-xs text-fg-muted">{settings.data?.work_dir || "-"}</div>
          </div>
          <div>
            <Label>Markdown 输出目录</Label>
            <div className="break-all text-xs text-fg-muted">{settings.data?.out_dir || "-"}</div>
          </div>
          <div>
            <Label>纳入的消息类型</Label>
            <div className="text-fg-muted">{settings.data?.text_types?.join(", ") || "-"}</div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
