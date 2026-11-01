/**
 * ECharts 不接受 Tailwind 类，配色得手动跟着主题走。
 * 这里集中放图表用到的色值，亮/暗各一套，跟 index.css 的令牌保持一致。
 */
import type { ResolvedTheme } from "@/components/theme-provider";

export function chartPalette(resolved: ResolvedTheme) {
  const dark = resolved === "dark";
  return {
    axisLabel: dark ? "#a1a1aa" : "#52525b",
    splitLine: dark ? "#27272a" : "#e4e4e7",
    label: dark ? "#d4d4d8" : "#3f3f46",
    sliceBorder: dark ? "#18181b" : "#ffffff",
    bar: "#38bdf8",
  };
}
