/**
 * Presentation for server job error categories.
 *
 * Error hygiene contract (job manager): the API sends a short category string
 * (e.g. exception class name or ``MissingApiKey``), never a raw exception
 * message. Known categories map to actionable human text here; unknown
 * categories pass through unchanged so nothing regresses when the backend
 * introduces a new one.
 */

const ERROR_CATEGORY_TEXT: Record<string, string> = {
  MissingApiKey:
    "No API key configured for the selected provider — set it in your environment or .env file (see README)",
};

/** Human-readable text for a job error category (passthrough when unknown). */
export function describeJobError(category: string): string {
  return ERROR_CATEGORY_TEXT[category] ?? category;
}
