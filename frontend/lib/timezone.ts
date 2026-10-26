export const APP_TIMEZONE = "Europe/Moscow";

export function appToday(date = new Date()): string {
  return date.toLocaleDateString("en-CA", { timeZone: APP_TIMEZONE });
}

export function lastAppDays(count: number, date = new Date()): string[] {
  const [year, month, day] = appToday(date).split("-").map(Number);
  const start = Date.UTC(year, month - 1, day);
  return Array.from({ length: count }, (_, index) =>
    new Date(start - index * 86_400_000).toISOString().slice(0, 10),
  );
}
