import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** 同源部署：dev 走 vite proxy，prod 由 FastAPI 直接托管 dist。 */
export const api = createClient<paths>({ baseUrl: "" });

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

export function fmtDay(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}
