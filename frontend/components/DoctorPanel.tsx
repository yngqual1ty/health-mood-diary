"use client";

import { useEffect, useState } from "react";
import { api, apiBlob, displayName } from "@/lib/api";
import type { Answer, DoctorPills, Mood, Patient, PatientDocument, Questionnaire, ResultSeries, TreatmentResponse } from "@/lib/types";
import { ResultsCharts } from "./ResultsCharts";

export function DoctorPanel() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [selected, setSelected] = useState<Patient | null>(null);
  const [moods, setMoods] = useState<Mood[]>([]);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [pills, setPills] = useState<DoctorPills | null>(null);
  const [documents, setDocuments] = useState<PatientDocument[]>([]);
  const [meta, setMeta] = useState<Record<string, Questionnaire>>({});
  const [invite, setInvite] = useState("");
  const [status, setStatus] = useState("");
  const [name, setName] = useState("");
  const [question, setQuestion] = useState("");
  const [treatment, setTreatment] = useState<TreatmentResponse | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadPatients() {
    const data = await api<{ items: Patient[] }>("/api/doctor/patients");
    setPatients(data.items);
    return data.items;
  }

  useEffect(() => {
    loadPatients().catch((error) => setStatus(error.message));
  }, []);

  async function openPatient(patient: Patient) {
    setSelected(patient);
    setName(displayName(patient));
    setTreatment(null);
    setPills(null);
    setDocuments([]);
    setStatus("");
    try {
      const [moodData, answerData, pillsData, docsData] = await Promise.all([
        api<{ items: Mood[] }>(`/api/doctor/patients/${patient.telegram_id}/moods`),
        api<{ items: Answer[]; questionnaires: Record<string, Questionnaire> }>(
          `/api/doctor/patients/${patient.telegram_id}/answers`,
        ),
        api<DoctorPills>(`/api/doctor/patients/${patient.telegram_id}/pills`),
        api<{ items: PatientDocument[] }>(`/api/doctor/patients/${patient.telegram_id}/documents`),
      ]);
      setMoods(moodData.items);
      setAnswers(answerData.items);
      setMeta(answerData.questionnaires);
      setPills(pillsData);
      setDocuments(docsData.items);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось загрузить пациента.");
    }
  }

  async function createInvite() {
    setStatus("Создание ссылки…");
    try {
      const data = await api<{ bot_username: string; start_payload: string }>(
        "/api/doctor/invites",
        { method: "POST" },
      );
      const link = `https://t.me/${data.bot_username}?start=${encodeURIComponent(data.start_payload)}`;
      setInvite(link);
      await navigator.clipboard.writeText(link).catch(() => undefined);
      setStatus("Ссылка создана и скопирована.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось создать ссылку.");
    }
  }

  async function saveName() {
    if (!selected || !name.trim()) return;
    await api(`/api/doctor/patients/${selected.telegram_id}`, {
      method: "POST",
      body: JSON.stringify({ display_name: name.trim() }),
    });
    await loadPatients();
    setStatus("Имя сохранено.");
  }

  async function unlink(patient: Patient) {
    if (!confirm(`Убрать пациента «${displayName(patient)}» из списка?`)) return;
    await api(`/api/doctor/patients/${patient.telegram_id}`, { method: "DELETE" });
    if (selected?.telegram_id === patient.telegram_id) setSelected(null);
    await loadPatients();
  }

  async function requestTreatment() {
    if (!selected || question.trim().length < 3) {
      setStatus("Опишите клиническую ситуацию.");
      return;
    }
    setBusy(true);
    setStatus("Поиск по рекомендациям…");
    try {
      const data = await api<TreatmentResponse>(
        `/api/doctor/patients/${selected.telegram_id}/treatment-options`,
        {
          method: "POST",
          body: JSON.stringify({ question, include_patient_data: true }),
        },
      );
      setTreatment(data);
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось получить варианты.");
    } finally {
      setBusy(false);
    }
  }

  if (selected) {
    const today = new Date().toISOString().slice(0, 10);
    const pastMoods = moods.filter((item) => item.created_at.slice(0, 10) < today);
    const pastAnswers = answers.filter((item) => item.created_at.slice(0, 10) < today);
    const series = buildSeries(pastAnswers, meta);
    return (
      <>
        <button className="secondary" onClick={() => setSelected(null)}>← К списку пациентов</button>
        <h1>{displayName(selected)}</h1>
        <section className="card">
          <label htmlFor="patientName">Имя пациента</label>
          <input id="patientName" value={name} onChange={(event) => setName(event.target.value)} />
          <button className="full" onClick={saveName}>Сохранить имя</button>
        </section>
        <PatientDocuments
          patientId={selected.telegram_id}
          documents={documents}
          onChange={setDocuments}
        />
        <ResultsCharts variant="doctor" moods={pastMoods} series={series} />
        <PillsAdherence pills={pills} />
        <section className="card">
          <h2>Варианты лечения по клиническим рекомендациям</h2>
          <p className="muted">Прототип поддержки решений. Варианты требуют клинической проверки.</p>
          <label htmlFor="treatmentQuestion">Клиническая ситуация</label>
          <textarea
            id="treatmentQuestion"
            rows={5}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button className="full" disabled={busy} onClick={requestTreatment}>
            Подобрать альтернативы
          </button>
          <div className="status">{status}</div>
          {treatment && (
            <div>
              {treatment.red_flags.map((flag) => (
                <p key={flag}><b>⚠️ {flag}</b></p>
              ))}
              {treatment.clinical_summary && <p>{treatment.clinical_summary}</p>}
              {treatment.alternatives.map((alt, index) => (
                <div className="entry" key={`${alt.title}-${index}`}>
                  <b>Вариант {index + 1}: {alt.title}</b>
                  <div className="muted">{alt.approach}</div>
                  {alt.rationale && <p>{alt.rationale}</p>}
                  {alt.cautions && <p><b>Ограничения:</b> {alt.cautions}</p>}
                  {alt.citations.length > 0 && (
                    <div className="muted">Источники: {alt.citations.join(", ")}</div>
                  )}
                </div>
              ))}
              {treatment.sources.length > 0 && (
                <details>
                  <summary>Фрагменты рекомендаций</summary>
                  {treatment.sources.map((source) => (
                    <div className="entry" key={source.id}>
                      <b>[{source.id}] {source.document} — {source.section}</b>
                      <p>{source.text}</p>
                    </div>
                  ))}
                </details>
              )}
              <p className="muted">{treatment.disclaimer}</p>
            </div>
          )}
        </section>
      </>
    );
  }

  return (
    <>
      <h1>Мои пациенты</h1>
      <section className="card">
        <button className="full" onClick={createInvite}>Пригласить пациента</button>
        {invite && <input readOnly value={invite} onClick={() => navigator.clipboard.writeText(invite)} />}
        <div className="status">{status}</div>
      </section>
      <section className="card">
        {!patients.length && <p className="muted">Пациентов пока нет.</p>}
        {patients.map((patient) => (
          <div className="entry row" key={patient.telegram_id}>
            <button className="secondary" style={{ flex: 1 }} onClick={() => openPatient(patient)}>
              {displayName(patient)}
              <div className="muted">
                {patient.last_mood == null ? "Нет записей" : `Настроение ${patient.last_mood}/5`}
                {" · "}анкет: {patient.answers_count}
              </div>
            </button>
            <button className="danger" onClick={() => unlink(patient)}>Убрать</button>
          </div>
        ))}
        <button className="full secondary" onClick={() => loadPatients()}>Обновить список</button>
      </section>
    </>
  );
}

function buildSeries(
  answers: Answer[],
  meta: Record<string, Questionnaire>,
): Record<string, ResultSeries> {
  const byId: Record<string, Record<string, Answer>> = {};
  answers.forEach((item) => {
    if (item.status !== "completed" || !meta[item.questionnaire_id]) return;
    const day = item.created_at.slice(0, 10);
    byId[item.questionnaire_id] ??= {};
    byId[item.questionnaire_id][day] = item;
  });
  const series: Record<string, ResultSeries> = {};
  Object.entries(byId).forEach(([qid, days]) => {
    const definition = meta[qid];
    series[qid] = {
      id: qid,
      title: definition.title || qid,
      scoring: definition.scoring,
      points: Object.keys(days).sort().map((day) => {
        const result = days[day].result || {};
        return {
          date: day,
          created_at: days[day].created_at,
          score: result.score,
          interpretation: result.interpretation,
          domains: result.domains,
        };
      }),
    };
  });
  return series;
}

const TAKEN_LABELS = {
  yes: "Принял",
  no: "Не принял",
  partial: "Частично",
} as const;

function lastUtcDays(count: number) {
  const now = new Date();
  return Array.from({ length: count }, (_, index) => {
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - index));
    return day.toISOString().slice(0, 10);
  });
}

function formatDocDate(value: string) {
  const [day, time] = value.split(" ");
  if (!day) return value;
  const date = day.split("-").reverse().join(".");
  return time ? `${date} ${time.slice(0, 5)}` : date;
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} КБ`;
  return `${Math.round(bytes / 104857.6) / 10} МБ`;
}

async function openPatientDocument(patientId: number, doc: PatientDocument, download: boolean) {
  const blob = await apiBlob(`/api/doctor/patients/${patientId}/documents/${doc.id}`);
  const url = URL.createObjectURL(blob);
  if (download) {
    const link = document.createElement("a");
    link.href = url;
    link.download = doc.original_name || "document";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function PatientDocuments({
  patientId,
  documents,
  onChange,
}: {
  patientId: number;
  documents: PatientDocument[];
  onChange: (items: PatientDocument[]) => void;
}) {
  const [status, setStatus] = useState("");

  async function open(doc: PatientDocument, download: boolean) {
    try {
      await openPatientDocument(patientId, doc, download);
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось открыть документ.");
    }
  }

  async function remove(doc: PatientDocument) {
    if (!confirm(`Удалить документ «${doc.original_name}»?`)) return;
    try {
      await api(`/api/doctor/patients/${patientId}/documents/${doc.id}`, { method: "DELETE" });
      onChange(documents.filter((item) => item.id !== doc.id));
      setStatus("Документ удалён.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось удалить документ.");
    }
  }

  return (
    <section className="card">
      <h2>Документы</h2>
      {!documents.length && (
        <p className="muted">Документов пока нет. Пациент может отправить файл боту в Telegram.</p>
      )}
      {documents.map((doc) => (
        <div className="entry" key={doc.id}>
          <b>{doc.original_name || "Документ"}</b>
          <div className="muted">
            {formatDocDate(doc.created_at)}
            {" · "}
            {formatSize(doc.size)}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="secondary" type="button" style={{ flex: 1 }} onClick={() => open(doc, false)}>Открыть</button>
            <button className="secondary" type="button" style={{ flex: 1 }} onClick={() => open(doc, true)}>Скачать</button>
            <button className="danger" type="button" style={{ flex: 1 }} onClick={() => remove(doc)}>Удалить</button>
          </div>
        </div>
      ))}
      {status && <div className="status">{status}</div>}
    </section>
  );
}

function PillsAdherence({ pills }: { pills: DoctorPills | null }) {
  if (!pills) {
    return (
      <section className="card">
        <h2>Лекарства</h2>
        <p className="muted">Загрузка схемы и отметок…</p>
      </section>
    );
  }
  const byDate = Object.fromEntries(pills.log.map((item) => [item.date, item]));
  return (
    <section className="card">
      <h2>Лекарства</h2>
      {!pills.configured && <p className="muted">Пациент ещё не заполнил схему.</p>}
      {pills.configured && pills.items.map((item, index) => {
        const extra = [item.time, item.note].filter(Boolean).join(", ");
        return (
          <div className="entry" key={`${item.name}-${index}`}>
            <b>{extra ? `${item.name} — ${extra}` : item.name}</b>
          </div>
        );
      })}
      {pills.configured && (
        <>
          <h2 style={{ marginTop: 16 }}>Отметки за 30 дней</h2>
          <p className="muted">Ежедневные ответы бота. Даты по UTC.</p>
          {lastUtcDays(30).map((day) => {
            const entry = byDate[day];
            return (
              <div className="entry row" key={day}>
                <span>{day.split("-").reverse().join(".")}</span>
                <span className="muted">{entry ? TAKEN_LABELS[entry.taken] : "Нет отметки"}</span>
              </div>
            );
          })}
        </>
      )}
    </section>
  );
}
