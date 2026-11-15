import { Pipe, PipeTransform } from '@angular/core';

const UNITS = ['byte', 'kilobyte', 'megabyte', 'gigabyte', 'terabyte', 'petabyte'] as const;

/** 1536 → "1.5 kB" — localised by the browser. */
export function formatBytes(bytes: number | null | undefined, perSecond = false): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—';
  let i = 0;
  let v = Math.abs(bytes);
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  const unit = perSecond ? `${UNITS[i]}-per-second` : UNITS[i];
  return new Intl.NumberFormat(undefined, {
    style: 'unit',
    unit,
    unitDisplay: 'short',
    maximumFractionDigits: v < 10 && i > 0 ? 1 : 0,
  }).format(Math.sign(bytes) * v);
}

export function percent(part: number, whole: number): string {
  if (!whole) return '—';
  return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(part / whole);
}

/** Unix seconds → "3 min ago" (falls back to a date after a day). */
export function ago(ts: number | null | undefined): string {
  if (!ts) return '—';
  const s = Math.round(Date.now() / 1000 - ts);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (s < 60) return rtf.format(-s, 'second');
  if (s < 3600) return rtf.format(-Math.round(s / 60), 'minute');
  if (s < 86400) return rtf.format(-Math.round(s / 3600), 'hour');
  return new Date(ts * 1000).toLocaleDateString();
}

@Pipe({ name: 'bytes' })
export class BytesPipe implements PipeTransform {
  transform(v: number | null | undefined, perSecond = false): string {
    return formatBytes(v, perSecond);
  }
}

@Pipe({ name: 'ago' })
export class AgoPipe implements PipeTransform {
  transform(v: number | null | undefined): string {
    return ago(v);
  }
}
