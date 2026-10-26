"use client";

import { useEffect, useState } from "react";
import { ResultsCharts } from "@/components/ResultsCharts";
import { api } from "@/lib/api";
import type { ResultsPayload } from "@/lib/types";

export default function HomePage() {
  const [data, setData] = useState<ResultsPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<ResultsPayload>("/api/me/results")
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Результаты</h1>
      <p className="muted">Графики за предыдущие дни. Сегодняшние ответы появятся здесь завтра.</p>
      {error && <div className="card">{error}</div>}
      {!data && !error && <div className="card muted">Загрузка графиков…</div>}
      {data && <ResultsCharts moods={data.moods} series={data.series} />}
    </>
  );
}
