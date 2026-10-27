export type Role = "patient" | "doctor";

export type Me = {
  telegram_id: number;
  role: Role;
  is_admin: boolean;
  first_name: string;
  last_name: string;
  username: string;
  display_name: string;
  linked: boolean;
  doctor_telegram_id: number | null;
  suicide_risk_completed: boolean;
};

export type Option = { value: number; label: string };
export type Question = {
  id: string;
  text: string;
  domain?: string;
  options: Option[];
};

export type Scoring =
  | { type: "sum" | "max"; bands: { max: number; label: string }[] }
  | {
      type: "domains";
      domains: Record<string, { title: string; bands: { max: number; label: string }[] }>;
    };

export type Questionnaire = {
  id: string;
  title: string;
  description?: string;
  instructions?: string;
  available?: boolean;
  items: Question[];
  scoring: Scoring;
  latest?: Answer | null;
  history?: Answer[];
};

export type Answer = {
  questionnaire_id: string;
  payload?: Record<string, number | string>;
  status: string;
  result: {
    type?: string;
    score?: number;
    interpretation?: string;
    domains?: Record<string, { score: number; interpretation: string }>;
  };
  created_at: string;
};

export type Mood = {
  score: number;
  emotions?: string;
  note?: string;
  created_at: string;
};

export type Allergies = {
  text: string;
  completed: boolean;
};

export type SleepQuality = "good" | "ok" | "bad";

export type SleepEntry = {
  date: string;
  quality: SleepQuality;
  hours: number | null;
  note: string;
  created_at: string;
};

export type PatientNote = {
  id: number;
  text: string;
  created_at: string;
};

export type SeriesPoint = {
  date: string;
  created_at?: string;
  score?: number;
  interpretation?: string;
  domains?: Record<string, { score: number; interpretation: string }>;
};

export type ResultSeries = {
  id: string;
  title: string;
  scoring: Scoring;
  points: SeriesPoint[];
};

export type ResultsPayload = {
  today: string;
  moods: Mood[];
  series: Record<string, ResultSeries>;
};

export type Patient = {
  telegram_id: number;
  first_name: string;
  last_name: string;
  username: string;
  display_name: string;
  last_mood: number | null;
  last_mood_at: string | null;
  answers_count: number;
};

export type Doctor = {
  telegram_id: number;
  first_name: string;
  last_name: string;
  username: string;
  display_name: string;
  is_admin: boolean;
};

export type PillItem = {
  name: string;
  time?: string;
  note?: string;
};

export type PillsRegimen = {
  configured: boolean;
  items: PillItem[];
  created_at?: string;
};

export type PillsLogEntry = {
  date: string;
  taken: "yes" | "no" | "partial";
  note: string;
  created_at: string;
};

export type DoctorPills = PillsRegimen & {
  log: PillsLogEntry[];
};

export type PatientDocument = {
  id: number;
  original_name: string;
  mime: string;
  size: number;
  created_at: string;
};

export type TreatmentAlternative = {
  title: string;
  approach: string;
  rationale: string;
  suitable_when: string;
  cautions: string;
  monitoring: string;
  evidence: string;
  citations: string[];
  grounded: boolean;
};

export type TreatmentResponse = {
  disclaimer: string;
  patient_context: string[];
  red_flags: string[];
  sources: {
    id: string;
    document: string;
    section: string;
    text: string;
  }[];
  alternatives: TreatmentAlternative[];
  missing_information: string[];
  clinical_summary: string;
  mode: string;
  model: string | null;
};
