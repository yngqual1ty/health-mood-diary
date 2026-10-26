"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_ORDER, DOMAIN_COLORS, formatDay } from "@/lib/questionnaires";
import type { Mood, ResultSeries } from "@/lib/types";

type ChartRow = { date: string; [key: string]: number | string };

function lineChart(
  rows: ChartRow[],
  lines: { key: string; color: string; name: string }[],
  variant?: "doctor",
) {
  if (!rows.length) return null;
  const tick = variant === "doctor" ? { fontSize: 12, fill: "#111" } : { fontSize: 12 };
  const legendStyle = variant === "doctor" ? { color: "#111" } : undefined;
  return (
    <div className="chart">
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid stroke="#e4eeed" vertical={false} />
          <XAxis dataKey="date" tickFormatter={formatDay} tick={tick} />
          <YAxis tick={tick} allowDecimals={false} />
          <Tooltip labelFormatter={(value) => String(value)} />
          <Legend
            wrapperStyle={legendStyle}
            formatter={
              variant === "doctor"
                ? (value) => <span style={{ color: "#111" }}>{value}</span>
                : undefined
            }
          />
          {lines.map((line) => (
            <Line
              key={line.key}
              type="monotone"
              dataKey={line.key}
              name={line.name}
              stroke={line.color}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ResultsCharts({
  moods,
  series,
  variant,
}: {
  moods: Mood[];
  series: Record<string, ResultSeries>;
  variant?: "doctor";
}) {
  const moodByDay = new Map<string, number>();
  moods.forEach((mood) => moodByDay.set(mood.created_at.slice(0, 10), mood.score));
  const moodRows = [...moodByDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, score]) => ({ date, score }));

  const blocks = CHART_ORDER.map((id) => series[id]).filter(Boolean);
  const captionClass = variant === "doctor" ? "muted chart-note" : "muted";
  if (!moodRows.length && !blocks.length) {
    return (
      <div className="card empty">
        <h2>Пока нет прошлых результатов</h2>
        <p className={captionClass}>
          На главной показаны только дни до сегодняшнего. Отметьте настроение или заполните
          HADS, BDI, BAI, ШПАНА или оценку суицидального риска — завтра они появятся на графике.
        </p>
      </div>
    );
  }

  return (
    <>
      {moodRows.length > 0 && (
        <section className="card">
          <h2>Настроение</h2>
          <p className={captionClass}>Оценка за предыдущие дни.</p>
          {lineChart(moodRows, [{ key: "score", color: "#087f79", name: "Настроение" }], variant)}
        </section>
      )}
      {blocks.map((item) => {
        const scoring = item.scoring;
        if (scoring && scoring.type === "domains") {
          const rows = item.points.map((point) => {
            const row: ChartRow = { date: point.date };
            Object.entries(point.domains || {}).forEach(([key, value]) => {
              row[key] = value.score;
            });
            return row;
          });
          const lines = Object.entries(scoring.domains).map(([key, config]) => ({
            key,
            name: config.title,
            color: DOMAIN_COLORS[key] || "#2d6f92",
          }));
          return (
            <section className="card" key={item.id}>
              <h2>{item.title}</h2>
              {lineChart(rows, lines, variant)}
            </section>
          );
        }
        const rows = item.points
          .filter((point) => point.score != null)
          .map((point) => ({ date: point.date, score: Number(point.score) }));
        return (
          <section className="card" key={item.id}>
            <h2>{item.title}</h2>
            {lineChart(rows, [{ key: "score", color: "#2d6f92", name: "Баллы" }], variant)}
          </section>
        );
      })}
    </>
  );
}
