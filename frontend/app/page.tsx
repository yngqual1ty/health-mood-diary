"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ResultsCharts } from "@/components/ResultsCharts";
import { ScaleList } from "@/components/ScaleList";
import { api } from "@/lib/api";
import { POST_OSR_SCALES, SUICIDE_RISK_SCALE } from "@/lib/questionnaires";
import type { Me, ResultsPayload } from "@/lib/types";

export default function HomePage() {
  const [me, setMe] = useState<Me | null>(null);
  const [data, setData] = useState<ResultsPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Me>("/api/me")
      .then(setMe)
      .catch((err) => setError(err.message));
    api<ResultsPayload>("/api/me/results")
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  const osrDone = Boolean(me?.suicide_risk_completed);

  return (
    <>
      <h1>Результаты</h1>
      <p className="muted">Графики за предыдущие дни. Сегодняшние ответы появятся здесь завтра.</p>
      {me && !osrDone && (
        <ScaleList
          title="Сначала оценка суицидального риска"
          hint="Остальные шкалы откроются после заполнения этой оценки."
          items={[SUICIDE_RISK_SCALE]}
          priority
        />
      )}
      {me && osrDone && (
        <ScaleList
          title="Опросники"
          hint="Заполните шкалы, когда будет удобно. Это не диагноз — так проще отслеживать самочувствие вместе с врачом."
          items={POST_OSR_SCALES}
        />
      )}
      <p>
        <Link href="/mood/" className="quiet-link">
          Отметить настроение
        </Link>
      </p>
      {error && <div className="card">{error}</div>}
      {!data && !error && <div className="card muted">Загрузка графиков…</div>}
      {data && <ResultsCharts moods={data.moods} series={data.series} />}
    </>
  );
}
