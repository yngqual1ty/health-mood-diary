"use client";

import Link from "next/link";
import type { PatientScaleLink } from "@/lib/questionnaires";

export function ScaleList({
  title,
  hint,
  items,
  priority,
}: {
  title?: string;
  hint?: string;
  items: PatientScaleLink[];
  priority?: boolean;
}) {
  return (
    <section className="scale-list" aria-label={title || "Опросники"}>
      {title && <h2>{title}</h2>}
      {hint && <p className="muted">{hint}</p>}
      {items.map((item) => (
        <Link
          key={item.id}
          href={item.href}
          className={priority ? "scale-card priority" : "scale-card"}
        >
          <strong>{item.title}</strong>
          <span>{item.blurb}</span>
        </Link>
      ))}
    </section>
  );
}
