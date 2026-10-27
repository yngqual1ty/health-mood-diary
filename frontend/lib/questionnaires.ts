export const CHART_ORDER = ["hads", "bdi", "bai", "panas", "suicide-risk"] as const;

export const DOMAIN_COLORS: Record<string, string> = {
  A: "#c47a2c",
  D: "#2d6f92",
  PA: "#087f79",
  NA: "#b4533a",
};

export type PatientScaleLink = {
  id: string;
  href: string;
  title: string;
  blurb: string;
};

export const SUICIDE_RISK_SCALE: PatientScaleLink = {
  id: "suicide-risk",
  href: "/suicide-risk/",
  title: "Оценка суицидального риска",
  blurb: "Суицидальный риск",
};

/** Suggested next after a completed ОСР. Telegram still sends these once as well. */
export const STARTER_SCALES: PatientScaleLink[] = [
  { id: "hads", href: "/hads/", title: "HADS", blurb: "Тревога и депрессия" },
  { id: "bai", href: "/bai/", title: "BAI", blurb: "Тревога" },
  { id: "bdi", href: "/bdi/", title: "BDI", blurb: "Депрессия" },
];

/** Telegram sends ШПАНА after the first of HADS/BAI/BDI; Mini App lists it with the other scales. */
export const PANAS_SCALE: PatientScaleLink = {
  id: "panas",
  href: "/panas/",
  title: "ШПАНА",
  blurb: "Позитивный и негативный аффект",
};

/** All Mini App scales; none require ОСР first. */
export const PATIENT_SCALES: PatientScaleLink[] = [
  SUICIDE_RISK_SCALE,
  ...STARTER_SCALES,
  PANAS_SCALE,
];

export function formatDay(iso: string) {
  const [year, month, day] = iso.slice(0, 10).split("-");
  if (!year || !month || !day) return iso;
  return `${day}.${month}`;
}
