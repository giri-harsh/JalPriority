import type { ReactNode } from "react";
import { describeError, isSessionExpired } from "../api";
import type { Tone } from "../format";
import s from "./shared.module.css";

const TONE_CLASS: Record<Tone, string> = {
  ok: s.toneOk,
  warn: s.toneWarn,
  danger: s.toneDanger,
  accent: s.toneAccent,
  neutral: s.toneNeutral,
};

export function toneClass(tone: Tone): string {
  return TONE_CLASS[tone];
}

interface BadgeProps {
  tone: Tone;
  children: ReactNode;
  title?: string;
  "data-testid"?: string;
  "data-solver-status"?: string;
  "data-plan-status"?: string;
  "data-alert-level"?: number;
}

/** Pill-shaped status label. */
export function Badge({ tone, children, title, ...data }: BadgeProps) {
  return (
    <span className={`${s.badge} ${TONE_CLASS[tone]}`} title={title} {...data}>
      {children}
    </span>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className={s.spinnerWrap} role="status" aria-live="polite">
      <span className={s.spinner} aria-hidden="true" />
      {label}
    </span>
  );
}

/** Inline error for a failed request. A 401 gets the fixed "session expired" wording. */
export function ErrorNotice({ error, what }: { error: unknown; what?: string }) {
  const expired = isSessionExpired(error);
  const message = describeError(error);
  return (
    <div
      className={`${s.notice} ${s.noticeDanger}`}
      role="alert"
      data-error-kind={expired ? "session-expired" : "request-failed"}
    >
      <span>{expired || !what ? message : `${what}: ${message}`}</span>
    </div>
  );
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className={s.chip} title={title}>
      {children}
    </span>
  );
}
