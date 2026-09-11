// Client-side validation mirroring backend rules (server/app/api/app.py).
// The backend re-validates and returns static 422s; this only improves UX.

import type { AssetType, Provider, RunConfig } from '../api/types';

export const TICKER_PATTERN = /^[A-Za-z0-9.\-:^]{1,16}$/;
export const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_INSTRUCTIONS = 4000;

export interface FormValues {
  ticker: string;
  date: string;
  asset_type: AssetType;
  provider: Provider;
  instructions: string;
}

export type FormErrors = Partial<Record<keyof FormValues | 'form', string>>;

/** Instructions are only valid with provider 'ta_plugins' (backend rule). */
export function instructionsEnabled(provider: Provider): boolean {
  return provider === 'ta_plugins';
}

/** C0/C1 control check mirrors backend validate_instructions (\n \r \t allowed). */
export function hasControlChars(text: string): boolean {
  for (const char of text) {
    if (char === '\n' || char === '\r' || char === '\t') continue;
    const code = char.charCodeAt(0);
    if (code < 32 || code === 127 || (code >= 128 && code <= 159)) return true;
  }
  return false;
}

export function validateForm(values: FormValues): FormErrors {
  const errors: FormErrors = {};

  const ticker = values.ticker.trim().toUpperCase();
  if (!ticker) {
    errors.ticker = 'Ticker is required.';
  } else if (!TICKER_PATTERN.test(ticker)) {
    errors.ticker = '1-16 chars: letters, digits, dot, dash, colon, caret.';
  }

  if (!values.date) {
    errors.date = 'Analysis date is required.';
  } else if (!DATE_PATTERN.test(values.date)) {
    errors.date = 'Date must be YYYY-MM-DD.';
  }

  if (instructionsEnabled(values.provider)) {
    if (values.instructions.length > MAX_INSTRUCTIONS) {
      errors.instructions = 'Instructions exceed 4000 characters.';
    } else if (values.instructions.length > 0 && hasControlChars(values.instructions)) {
      errors.instructions = 'Instructions contain invalid control characters.';
    }
  }

  return errors;
}

export function formIsValid(values: FormValues): boolean {
  const errors = validateForm(values);
  return Object.keys(errors).length === 0;
}

/** Build the API payload from form values; trims and normalizes. */
export function toRunConfig(values: FormValues): RunConfig {
  const config: RunConfig = {
    ticker: values.ticker.trim().toUpperCase(),
    date: values.date,
    asset_type: values.asset_type,
    provider: values.provider,
  };
  if (instructionsEnabled(values.provider) && values.instructions.trim().length > 0) {
    config.instructions = values.instructions.trim();
  }
  return config;
}
