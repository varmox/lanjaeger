import { Component, computed, inject, signal } from '@angular/core';
import { ClrDatagridModule } from '@clr/angular/data/datagrid';
import { ClrIconModule } from '@clr/angular/icon';

import { Adblock, Api, Disk, Service, Stats } from '../shared/api';
import { AgoPipe, BytesPipe, percent } from '../shared/format';
import { poll } from '../shared/poll';
import { TrafficChart } from '../shared/traffic-chart';

@Component({
  selector: 'lj-dashboard',
  imports: [ClrDatagridModule, ClrIconModule, BytesPipe, AgoPipe, TrafficChart],
  template: `
    <h1>Dashboard</h1>
    @if (error()) {
      <div class="alert alert-danger" role="alert">
        <div class="alert-items"><div class="alert-item static"><span class="alert-text">{{ error() }}</span></div></div>
      </div>
    }

    @for (a of activeAlerts(); track a.title) {
      <div class="alert" [class.alert-danger]="a.severity === 'critical'" [class.alert-warning]="a.severity !== 'critical'" role="alert">
        <div class="alert-items"><div class="alert-item static">
          <div class="alert-icon-wrapper"><cds-icon class="alert-icon" shape="exclamation-triangle" aria-hidden="true"></cds-icon></div>
          <span class="alert-text">{{ a.title }}</span>
        </div></div>
      </div>
    }

    @if (stats(); as s) {
      <div class="lj-cards">
        <div class="card">
          <div class="card-block">
            <p class="card-text lj-kpi-label">Cache hit rate</p>
            <p class="lj-kpi">{{ hitRate() }}</p>
            <p class="card-text">of {{ s.total.hitBytes + s.total.missBytes | bytes }} served</p>
          </div>
        </div>
        <div class="card">
          <div class="card-block">
            <p class="card-text lj-kpi-label">Internet traffic saved</p>
            <p class="lj-kpi">{{ s.total.hitBytes | bytes }}</p>
            <p class="card-text">since {{ s.since | ago }}</p>
          </div>
        </div>
        <div class="card">
          <div class="card-block">
            <p class="card-text lj-kpi-label">Downloading now</p>
            <p class="lj-kpi">{{ activeRate() | bytes: true }}</p>
            <p class="card-text">{{ s.active.length }} active {{ s.active.length === 1 ? 'download' : 'downloads' }}</p>
          </div>
        </div>
        @if (adblock()?.available) {
          <div class="card">
            <div class="card-block">
              <p class="card-text lj-kpi-label">Ads blocked (24 h)</p>
              <p class="lj-kpi">{{ number(adblock()?.summary?.blocked) }}</p>
              <p class="card-text">{{ adblockShare() }} of DNS queries{{ adblock()?.active ? '' : ' — blocking is off' }}</p>
            </div>
          </div>
        }
        <div class="card">
          <div class="card-block">
            <label class="card-text lj-kpi-label" for="disk-progress">Cache disk</label>
            @if (disk(); as d) {
              <p class="lj-kpi">{{ d.cacheBytes | bytes }}</p>
              <progress id="disk-progress" class="lj-progress" [max]="d.cacheLimit ?? 1" [value]="d.cacheBytes ?? 0"
                        [attr.aria-valuetext]="diskText()"></progress>
              <p class="card-text">{{ diskText() }}</p>
            } @else {
              <p class="lj-kpi">—</p>
            }
          </div>
        </div>
      </div>

      <section class="lj-section" aria-labelledby="traffic-h">
        <h2 id="traffic-h">Traffic</h2>
        <lj-traffic-chart [series]="s.series" />
      </section>

      @if (services().length) {
        <section class="lj-section" aria-labelledby="health-h">
          <h2 id="health-h">Health</h2>
          <ul class="lj-health">
            @for (svc of services(); track svc.id) {
              <li>
                <cds-icon [attr.shape]="healthIcon(svc)" [attr.status]="healthStatus(svc)" aria-hidden="true"></cds-icon>
                <strong>{{ svc.service }}</strong>
                <span>{{ svc.health ?? svc.state }}</span>
              </li>
            }
          </ul>
        </section>
      }

      <section class="lj-section" aria-labelledby="services-h">
        <h2 id="services-h">By service</h2>
        <clr-datagrid>
          <clr-dg-column [clrDgField]="'name'">Service</clr-dg-column>
          <clr-dg-column [clrDgField]="'hitBytes'">From cache</clr-dg-column>
          <clr-dg-column [clrDgField]="'missBytes'">From internet</clr-dg-column>
          <clr-dg-column>Hit rate</clr-dg-column>
          <clr-dg-column [clrDgField]="'lastSeen'">Last activity</clr-dg-column>
          <clr-dg-row *clrDgItems="let row of s.services" [clrDgItem]="row">
            <clr-dg-cell>{{ row.name }}</clr-dg-cell>
            <clr-dg-cell>{{ row.hitBytes | bytes }}</clr-dg-cell>
            <clr-dg-cell>{{ row.missBytes | bytes }}</clr-dg-cell>
            <clr-dg-cell>{{ rate(row.hitBytes, row.missBytes) }}</clr-dg-cell>
            <clr-dg-cell>{{ row.lastSeen | ago }}</clr-dg-cell>
          </clr-dg-row>
          <clr-dg-placeholder>No downloads yet. Start one on a client that uses the cache's DNS.</clr-dg-placeholder>
          <clr-dg-footer>{{ s.services.length }} services</clr-dg-footer>
        </clr-datagrid>
      </section>

      @if (s.active.length) {
        <section class="lj-section" aria-labelledby="active-h">
          <h2 id="active-h">Active downloads (last minute)</h2>
          <clr-datagrid>
            <clr-dg-column>Client</clr-dg-column>
            <clr-dg-column>Service</clr-dg-column>
            <clr-dg-column>Speed</clr-dg-column>
            <clr-dg-row *clrDgItems="let a of s.active">
              <clr-dg-cell>{{ a.client }}</clr-dg-cell>
              <clr-dg-cell>{{ a.service }}</clr-dg-cell>
              <clr-dg-cell>{{ a.bytesPerSecond | bytes: true }}</clr-dg-cell>
            </clr-dg-row>
          </clr-datagrid>
        </section>
      }
    } @else if (!error()) {
      <span class="spinner spinner-inline">Loading…</span> Loading statistics…
    }
  `,
})
export class DashboardPage {
  private readonly api = inject(Api);
  protected readonly stats = signal<Stats | null>(null);
  protected readonly disk = signal<Disk | null>(null);
  protected readonly services = signal<Service[]>([]);
  protected readonly error = signal('');
  protected readonly adblock = signal<Adblock | null>(null);
  protected readonly activeAlerts = signal<{ title: string; severity: string }[]>([]);
  protected readonly adblockShare = computed(() => {
    const s = this.adblock()?.summary;
    return s ? percent(s.blocked, s.queries) : '—';
  });

  protected readonly hitRate = computed(() => {
    const t = this.stats()?.total;
    return t ? percent(t.hitBytes, t.hitBytes + t.missBytes) : '—';
  });
  protected readonly activeRate = computed(() =>
    (this.stats()?.active ?? []).reduce((sum, a) => sum + a.bytesPerSecond, 0),
  );
  protected readonly diskText = computed(() => {
    const d = this.disk();
    if (!d) return '';
    if (d.cacheBytes == null) return 'Measuring cache size…';
    return d.cacheLimit ? `${percent(d.cacheBytes, d.cacheLimit)} of the configured limit` : '';
  });

  constructor() {
    poll(() => this.load(), 5000);
    poll(() => this.api.disk().then((d) => this.disk.set(d)).catch(() => undefined), 60_000);
    poll(() => this.api.adblock().then((a) => this.adblock.set(a)).catch(() => undefined), 30_000);
    poll(() => this.api.activeAlerts().then((a) => this.activeAlerts.set(a.active)).catch(() => undefined), 30_000);
  }

  private async load() {
    try {
      const [s, svc] = await Promise.all([this.api.stats(), this.api.services()]);
      this.stats.set(s);
      this.services.set(svc.services);
      this.error.set('');
    } catch (e) {
      this.error.set('Could not load statistics — retrying.');
    }
  }

  protected number(n: number | null | undefined) {
    return n == null ? '—' : new Intl.NumberFormat().format(n);
  }

  protected rate(hit: number, miss: number) {
    return percent(hit, hit + miss);
  }

  protected healthIcon(s: Service) {
    if (s.state !== 'running' || s.health === 'unhealthy') return 'times-circle';
    return s.health === 'starting' ? 'exclamation-triangle' : 'check-circle';
  }

  protected healthStatus(s: Service) {
    if (s.state !== 'running' || s.health === 'unhealthy') return 'danger';
    return s.health === 'starting' ? 'warning' : 'success';
  }
}
