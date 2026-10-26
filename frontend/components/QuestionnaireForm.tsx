"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ScaleList } from "@/components/ScaleList";
import { api } from "@/lib/api";
import { POST_OSR_SCALES } from "@/lib/questionnaires";
import type { Me, Questionnaire } from "@/lib/types";

const REQUIRES_SUICIDE_RISK = new Set(["hads", "bai", "bdi", "panas"]);

function ResultView({ questionnaire }: { questionnaire: Questionnaire }) {
  const result = questionnaire.latest?.result;
  if (!result) return null;
  const scoring = questionnaire.scoring;
  if (result.type === "domains") {
    return (
      <div className="score-grid">
        {Object.entries(result.domains || {}).map(([key, value]) => (
          <div className="score-tile" key={key}>
            <strong>
              {"domains" in scoring ? scoring.domains[key]?.title || key : key}
            </strong>
            <span>{value.score}</span>
            <div className="muted">{value.interpretation}</div>
          </div>
        ))}
      </div>
    );
  }
  if (result.type === "sum") {
    return (
      <div className="score-grid">
        <div className="score-tile">
          <strong>Результат</strong>
          <span>{result.score}</span>
          <div className="muted">{result.interpretation}</div>
        </div>
      </div>
    );
  }
  return null;
}

export function QuestionnaireForm({ questionnaireId }: { questionnaireId: string }) {
  const [data, setData] = useState<Questionnaire | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setBlocked(false);
    setStatus("");
    setJustSaved(false);
    async function load() {
      try {
        if (REQUIRES_SUICIDE_RISK.has(questionnaireId)) {
          const me = await api<Me>("/api/me");
          if (!me.suicide_risk_completed) {
            if (!cancelled) setBlocked(true);
            return;
          }
        }
        const next = await api<Questionnaire>(`/api/questionnaires/${questionnaireId}`);
        if (!cancelled) setData(next);
      } catch (error) {
        if (!cancelled) {
          setStatus(error instanceof Error ? error.message : "Не удалось загрузить опросник.");
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [questionnaireId]);

  if (blocked) {
    return (
      <>
        <h1>Сначала оценка суицидального риска</h1>
        <section className="card">
          <p>Эта шкала откроется после заполнения оценки суицидального риска.</p>
          <p>
            <Link href="/suicide-risk/" className="quiet-link">
              Открыть оценку суицидального риска
            </Link>
          </p>
        </section>
      </>
    );
  }

  if (!data && status) return <div className="card">{status}</div>;
  if (!data) return <div className="card muted">Загрузка опросника…</div>;

  async function submit() {
    if (!data) return;
    if (!data.items.every((item) => answers[item.id] != null)) {
      setStatus("Ответьте на все вопросы.");
      return;
    }
    setSaving(true);
    setStatus("Сохранение…");
    try {
      await api("/api/answers", {
        method: "POST",
        body: JSON.stringify({
          questionnaire_id: questionnaireId,
          answers,
          status: "completed",
        }),
      });
      const next = await api<Questionnaire>(`/api/questionnaires/${questionnaireId}`);
      setData(next);
      setAnswers({});
      setJustSaved(true);
      setStatus("Ответы сохранены. Сегодняшний результат появится на графике со следующего дня.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить.");
    } finally {
      setSaving(false);
    }
  }

  const showNextScales = questionnaireId === "suicide-risk" && justSaved;

  return (
    <>
      <h1>{data.title}</h1>
      {data.description && <p className="muted">{data.description}</p>}
      {showNextScales && (
        <>
          <ScaleList
            title="Следующие шкалы"
            hint="Пожалуйста, заполните три стартовые шкалы. Это не диагноз — так проще отслеживать самочувствие вместе с врачом."
            items={POST_OSR_SCALES}
          />
          <p>
            <Link href="/" className="quiet-link">
              К результатам
            </Link>
          </p>
        </>
      )}
      {data.latest && (
        <section className="card">
          <h2>Последний результат</h2>
          <p className="muted">{data.latest.created_at}</p>
          <ResultView questionnaire={data} />
        </section>
      )}
      <section className="card">
        {data.instructions && <p>{data.instructions}</p>}
        {data.items.map((item, index) => (
          <fieldset key={item.id} style={{ border: 0, margin: 0, padding: 0 }}>
            <label>{index + 1}. {item.text}</label>
            {item.options.map((option) => (
              <label className="option" key={`${item.id}-${option.value}`}>
                <input
                  type="radio"
                  name={item.id}
                  value={option.value}
                  checked={answers[item.id] === option.value}
                  onChange={() => setAnswers((current) => ({ ...current, [item.id]: option.value }))}
                />
                {option.label}
              </label>
            ))}
          </fieldset>
        ))}
        <button className="full" disabled={saving} onClick={submit}>
          Сохранить ответы
        </button>
        <div className="status">{status}</div>
      </section>
    </>
  );
}
