export const CHART_ORDER = ["hads", "bdi", "bai", "panas"] as const;

export const DOMAIN_COLORS: Record<string, string> = {
  A: "#c47a2c",
  D: "#2d6f92",
  PA: "#087f79",
  NA: "#b4533a",
};

export function formatDay(iso: string) {
  const [year, month, day] = iso.slice(0, 10).split("-");
  if (!year || !month || !day) return iso;
  return `${day}.${month}`;
}
