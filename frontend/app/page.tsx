"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ResultsCharts } from "@/components/ResultsCharts";
import { ScaleList } from "@/components/ScaleList";
import { AllergyPrompt } from "@/components/AllergyPrompt";
import { api } from "@/lib/api";
import { PATIENT_SCALES } from "@/lib/questionnaires";
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
      <AllergyPrompt />
      <h1>Результаты</h1>
      <p className="muted">Графики за предыдущие дни. Сегодняшние ответы появятся здесь завтра.</p>
      <ScaleList
        title="Опросники"
        hint="Заполните шкалы, когда будет удобно. Это не диагноз — так проще отслеживать самочувствие вместе с врачом."
        items={PATIENT_SCALES}
      />
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
