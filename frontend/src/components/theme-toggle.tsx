/**
 * 亮/暗切换控件：亮色 / 暗色 / 跟随系统 三档，直接对应 ThemeProvider 的三种偏好。
 */
import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme, type Theme } from "@/components/theme-provider";

const OPTIONS: { value: Theme; label: string; icon: LucideIcon }[] = [
  { value: "light", label: "亮色", icon: Sun },
  { value: "dark", label: "暗色", icon: Moon },
  { value: "system", label: "跟随系统", icon: Monitor },
];

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <div
      role="group"
      aria-label="界面配色"
      className="flex items-center gap-0.5 rounded-md border border-line bg-surface-2/60 p-0.5"
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            title={label}
            aria-label={label}
            aria-pressed={active}
            onClick={() => setTheme(value)}
            className={cn(
              "inline-flex h-6 w-6 items-center justify-center rounded transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-line-strong",
              active
                ? "bg-surface text-fg-strong shadow-sm"
                : "text-fg-subtle hover:text-fg-muted",
            )}
          >
            <Icon size={13} />
          </button>
        );
      })}
    </div>
  );
}
