export function fmtDate(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export const chLabel = (ch: string) => (ch === "in_app" ? "in-app" : ch);
export const STATUS_COLOR: Record<string, string> = {
  delivered: "green", completed: "green", dispatched: "blue", sent: "blue",
  processing: "blue", created: "", pending: "", retrying: "amber", failed: "red", dead: "red",
};
