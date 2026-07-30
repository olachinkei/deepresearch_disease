export const MECHANISMS = [
  "stabilization",
  "inhibition",
  "degradation",
  "activation",
  "other",
] as const;

export const FEEDBACK_REASONS = [
  "irrelevant_sources",
  "unsupported_claim",
  "incomplete",
  "citation_error",
  "too_slow",
  "other",
] as const;

export const TURN_STATUSES = [
  "running",
  "completed",
  "cancelled",
  "error",
] as const;

export const FEEDBACK_SYNC_STATUSES = [
  "pending",
  "syncing",
  "synced",
  "failed",
] as const;

export const DISEASE = "ischemic stroke" as const;
