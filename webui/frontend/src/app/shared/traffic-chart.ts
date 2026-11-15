import { Component, computed, input } from '@angular/core';

import { formatBytes } from './format';

interface Bar {
  x: number;
  hitH: number;
  missH: number;
  label: string;
}

/**
 * Last 24 h as hourly stacked bars (cache hits vs. misses), plain SVG.
 * The SVG is decorative for assistive tech; the same numbers are in the
 * summary sentence and in the totals cards.
 */
@Component({
  selector: 'lj-traffic-chart',
  template: `
    <figure class="lj-chart">
      <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" preserveAspectRatio="none" aria-hidden="true">
        @for (b of bars(); track b.x) {
          <g>
            <title>{{ b.label }}</title>
            <rect class="miss" [attr.x]="b.x" [attr.y]="height - b.hitH - b.missH" [attr.width]="barW" [attr.height]="b.missH" />
            <rect class="hit" [attr.x]="b.x" [attr.y]="height - b.hitH" [attr.width]="barW" [attr.height]="b.hitH" />
          </g>
        }
      </svg>
      <figcaption>
        <span class="lj-key hit">From cache</span>
        <span class="lj-key miss">From internet</span>
        <span class="lj-summary">{{ summary() }}</span>
      </figcaption>
    </figure>
  `,
})
export class TrafficChart {
  readonly series = input.required<{ t: number; hit: number; miss: number }[]>();

  protected readonly width = 960;
  protected readonly height = 160;
  protected readonly barW = 34;

  private readonly hours = computed(() => {
    const byHour = new Map<number, { hit: number; miss: number }>();
    for (const p of this.series()) {
      const h = Math.floor(p.t / 3600) * 3600;
      const slot = byHour.get(h) ?? { hit: 0, miss: 0 };
      slot.hit += p.hit;
      slot.miss += p.miss;
      byHour.set(h, slot);
    }
    return [...byHour.entries()].sort((a, b) => a[0] - b[0]).slice(-24);
  });

  protected readonly bars = computed<Bar[]>(() => {
    const hours = this.hours();
    const max = Math.max(1, ...hours.map(([, v]) => v.hit + v.miss));
    const step = this.width / 24;
    const fmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
    return hours.map(([t, v], i) => ({
      x: i * step + (step - this.barW) / 2,
      hitH: (v.hit / max) * (this.height - 4),
      missH: (v.miss / max) * (this.height - 4),
      label: `${fmt.format(t * 1000)}: ${formatBytes(v.hit)} from cache, ${formatBytes(v.miss)} from internet`,
    }));
  });

  protected readonly summary = computed(() => {
    const hit = this.hours().reduce((s, [, v]) => s + v.hit, 0);
    const miss = this.hours().reduce((s, [, v]) => s + v.miss, 0);
    return `Last 24 hours: ${formatBytes(hit)} served from cache, ${formatBytes(miss)} downloaded from the internet.`;
  });
}
