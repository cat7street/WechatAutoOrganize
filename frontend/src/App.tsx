import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  ListChecks,
  MessagesSquare,
  PlayCircle,
  Settings as SettingsIcon,
} from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import Overview from "@/pages/Overview";
import Sessions from "@/pages/Sessions";
import Tasks from "@/pages/Tasks";
import Run from "@/pages/Run";
import Settings from "@/pages/Settings";

const NAV = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/sessions", label: "会话白名单", icon: MessagesSquare },
  { to: "/tasks", label: "待办列表", icon: ListChecks },
  { to: "/run", label: "运行", icon: PlayCircle },
  { to: "/settings", label: "设置", icon: SettingsIcon },
];

function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-surface">
      <div className="px-4 py-5">
        <div className="text-sm font-semibold text-fg-strong">微信待办</div>
        <div className="mt-0.5 text-[11px] text-fg-subtle">WechatAutoOrganize</div>
      </div>
      <nav className="flex flex-col gap-1 px-2">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-surface-2 text-fg-strong"
                  : "text-fg-muted hover:bg-surface-2 hover:text-fg-strong",
              )
            }
          >
            <Icon size={15} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto px-4 py-4 text-[11px] text-fg-faint">
        本地运行 · 数据不出机器
      </div>
    </aside>
  );
}

function StatusBar() {
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await api.GET("/api/health")).data,
    refetchInterval: 15000,
  });
  return (
    <header className="flex h-12 shrink-0 items-center justify-end gap-2 border-b border-line px-4">
      {data?.running ? <Badge tone="info">运行中</Badge> : <Badge tone="ok">空闲</Badge>}
      <Badge tone={data?.decrypted ? "ok" : "medium"}>
        {data?.decrypted ? "已解密" : "未解密"}
      </Badge>
      <Badge tone={data?.llm_configured ? "ok" : "high"}>
        {data?.llm_configured ? "模型已配置" : "模型未配置"}
      </Badge>
      <Badge tone="low">白名单 {data?.whitelist_count ?? 0}</Badge>
      <span aria-hidden className="mx-1 h-4 w-px bg-line" />
      <ThemeToggle />
    </header>
  );
}

export default function App() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <StatusBar />
        <main className="min-h-0 flex-1 overflow-auto p-5">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/run" element={<Run />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
