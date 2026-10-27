export const APP_TIMEZONE = "Europe/Moscow";

function hasExplicitZone(value: string): boolean {
  return /[zZ]$|[+-]\d{2}:\d{2}$/.test(value.trim());
}

/** Parse API timestamps. Naive `YYYY-MM-DD HH:MM:SS` is UTC (SQLite CURRENT_TIMESTAMP). */
export function parseTimestamp(value: string | Date): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  const text = value.trim();
  if (!text) return null;
  const normalized = text.includes("T") ? text : text.replace(" ", "T");
  const date = new Date(hasExplicitZone(text) ? text : `${normalized}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function appToday(date = new Date()): string {
  return date.toLocaleDateString("en-CA", { timeZone: APP_TIMEZONE });
}

export function appDay(value: string | Date): string {
  const date = value instanceof Date ? value : parseTimestamp(value);
  if (!date) return typeof value === "string" ? value.slice(0, 10) : "";
  return date.toLocaleDateString("en-CA", { timeZone: APP_TIMEZONE });
}

export function lastAppDays(count: number, date = new Date()): string[] {
  const [year, month, day] = appToday(date).split("-").map(Number);
  const start = Date.UTC(year, month - 1, day);
  return Array.from({ length: count }, (_, index) =>
    new Date(start - index * 86_400_000).toISOString().slice(0, 10),
  );
}

export function formatAppDateTime(value: string): string {
  const date = parseTimestamp(value);
  if (!date) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: APP_TIMEZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatAppRelative(value: string): string {
  const date = parseTimestamp(value);
  if (!date) return value;
  const diffSec = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diffSec);
  const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
  if (abs < 45) return rtf.format(0, "second");
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 7) return rtf.format(Math.round(diffSec / 86400), "day");
  return formatAppDateTime(value);
}
