import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ClrDatagridModule } from '@clr/angular/data/datagrid';
import { ClrIcon } from '@clr/angular/icon';

import { Adblock, Api, errorMessage } from '../shared/api';
import { percent } from '../shared/format';
import { poll } from '../shared/poll';

type ListKind = 'allow' | 'deny';

@Component({
  selector: 'lj-adblock',
  imports: [FormsModule, ClrDatagridModule, ClrIcon],
  template: `
    <h1>Ad blocking</h1>
    <p>Every device using this cache's DNS gets ads, trackers and malware domains blocked — no app needed.</p>

    <p class="lj-status" role="status">{{ message() }}</p>

    @if (data(); as d) {
      @if (!d.available) {
        <div class="alert alert-warning" role="alert">
          <div class="alert-items"><div class="alert-item static"><span class="alert-text">
            The ad blocker (blocky) isn't reachable: {{ d.error }}
          </span></div></div>
        </div>
      } @else {
        <div class="lj-cards">
          <div class="card">
            <div class="card-block">
              <p class="card-text lj-kpi-label">Status</p>
              <p class="lj-kpi">
                <cds-icon [shape]="d.active ? 'shield-check' : 'shield'" size="lg"
                          [status]="d.active ? 'success' : 'warning'" aria-hidden="true"></cds-icon>
                {{ d.active ? 'Blocking' : (d.pausedForSeconds ? 'Paused' : 'Off') }}
              </p>
              <p class="card-text">
                @if (d.pausedForSeconds) { Resumes in {{ minutesLeft(d.pausedForSeconds) }} min }
                @else if (!d.persistedEnabled) { Stays off after restarts }
                @else { Lists refresh daily }
              </p>
            </div>
          </div>
          <div class="card">
            <div class="card-block">
              <p class="card-text lj-kpi-label">Blocked (24 h)</p>
              <p class="lj-kpi">{{ count(d.summary?.blocked) }}</p>
              <p class="card-text">{{ blockedShare() }} of {{ count(d.summary?.queries) }} queries</p>
            </div>
          </div>
          <div class="card">
            <div class="card-block">
              <p class="card-text lj-kpi-label">DNS cache hit rate</p>
              <p class="lj-kpi">{{ cacheRate() }}</p>
              <p class="card-text">average answer {{ d.summary?.avgResponseMs ?? '—' }} ms</p>
            </div>
          </div>
          <div class="card">
            <div class="card-block">
              <p class="card-text lj-kpi-label">Domains on blocklists</p>
              <p class="lj-kpi">{{ count(listSize()) }}</p>
              <p class="card-text">Steven Black hosts + your denylist</p>
            </div>
          </div>
        </div>

        @if (signedIn()) {
          <section class="lj-section" aria-labelledby="control-h">
            <h2 id="control-h">Control</h2>
            <div class="lj-controls">
              <div class="clr-toggle-wrapper">
                <input type="checkbox" role="switch" id="adblock-switch" class="clr-toggle"
                       [checked]="d.persistedEnabled" (change)="setEnabled($any($event.target).checked)" [disabled]="busy()">
                <label for="adblock-switch">Block ads and trackers</label>
              </div>
              <div class="btn-group" role="group" aria-label="Pause blocking">
                @for (m of [5, 30, 60]; track m) {
                  <button type="button" class="btn btn-sm btn-outline" (click)="pause(m)" [disabled]="busy() || !d.active">
                    <cds-icon shape="pause" aria-hidden="true"></cds-icon> {{ m }} min
                  </button>
                }
              </div>
            </div>
            <p class="lj-muted">A pause ends by itself; the switch stays as set across restarts.
              The Firefox/iCloud Private Relay canaries stay active either way, so devices keep using this DNS and the cache.</p>
          </section>
        }

        <div class="lj-two-col">
          <section class="lj-section" aria-labelledby="top-blocked-h">
            <h2 id="top-blocked-h">Most blocked (24 h)</h2>
            <clr-datagrid>
              <clr-dg-column>Domain</clr-dg-column>
              <clr-dg-column>Queries</clr-dg-column>
              @if (signedIn()) { <clr-dg-column>Action</clr-dg-column> }
              <clr-dg-row *clrDgItems="let row of d.topBlockedDomains ?? []">
                <clr-dg-cell><code>{{ row.name }}</code></clr-dg-cell>
                <clr-dg-cell>{{ count(row.count) }}</clr-dg-cell>
                @if (signedIn()) {
                  <clr-dg-cell>
                    <button type="button" class="btn btn-sm btn-link" (click)="allow(row.name)" [disabled]="busy()">
                      Allow
                    </button>
                  </clr-dg-cell>
                }
              </clr-dg-row>
              <clr-dg-placeholder>Nothing blocked yet.</clr-dg-placeholder>
            </clr-datagrid>
          </section>

          <section class="lj-section" aria-labelledby="top-clients-h">
            <h2 id="top-clients-h">Most active devices (24 h)</h2>
            <clr-datagrid>
              <clr-dg-column>Device</clr-dg-column>
              <clr-dg-column>DNS queries</clr-dg-column>
              <clr-dg-row *clrDgItems="let row of d.topClients ?? []">
                <clr-dg-cell>{{ row.name }}</clr-dg-cell>
                <clr-dg-cell>{{ count(row.count) }}</clr-dg-cell>
              </clr-dg-row>
              <clr-dg-placeholder>No queries yet.</clr-dg-placeholder>
            </clr-datagrid>
          </section>
        </div>

        @if (signedIn()) {
          <section class="lj-section" aria-labelledby="lists-h">
            <h2 id="lists-h">Your lists</h2>
            <p>One domain per line; subdomains are included. <code>*.example.com</code> works too.</p>
            <div class="lj-two-col">
              @for (kind of kinds; track kind) {
                <form class="clr-form clr-form-vertical" (ngSubmit)="saveList(kind)">
                  <div class="clr-form-control" [class.clr-error]="listErrors()[kind]">
                    <label class="clr-control-label" [for]="kind + '-list'">{{ kind === 'allow' ? 'Allowlist (never block)' : 'Denylist (always block)' }}</label>
                    <div class="clr-control-container">
                      <textarea class="clr-textarea lj-list" [id]="kind + '-list'" [name]="kind" rows="8"
                                spellcheck="false" autocapitalize="off" autocomplete="off"
                                [ngModel]="lists()[kind]" (ngModelChange)="setList(kind, $event)" [attr.aria-describedby]="kind + '-err'"
                                [attr.aria-invalid]="listErrors()[kind] ? 'true' : null"></textarea>
                      <span class="clr-subtext lj-field-error" [id]="kind + '-err'">{{ listErrors()[kind] }}</span>
                    </div>
                  </div>
                  <button type="submit" class="btn btn-sm btn-primary" [disabled]="busy()">Save {{ kind }}list</button>
                </form>
              }
            </div>
          </section>
        }
      }
    } @else {
      <span class="spinner spinner-inline">Loading…</span> Loading…
    }
  `,
})
export class AdblockPage {
  private readonly api = inject(Api);
  protected readonly data = signal<Adblock | null>(null);
  protected readonly busy = signal(false);
  protected readonly message = signal('');
  protected readonly listErrors = signal<Partial<Record<ListKind, string>>>({});
  protected readonly signedIn = computed(() => !!this.api.session()?.signedIn);
  protected readonly kinds: ListKind[] = ['allow', 'deny'];
  protected readonly lists = signal<Record<ListKind, string>>({ allow: '', deny: '' });

  protected readonly blockedShare = computed(() => {
    const s = this.data()?.summary;
    return s ? percent(s.blocked, s.queries) : '—';
  });
  protected readonly cacheRate = computed(() => {
    const r = this.data()?.summary?.cacheHitRate;
    return r == null ? '—' : percent(r > 1 ? r / 100 : r, 1);
  });
  protected readonly listSize = computed(() =>
    Object.values(this.data()?.lists?.denylist ?? {}).reduce((a, b) => a + b, 0),
  );

  constructor() {
    poll(() => this.load(), 10_000);
    if (this.signedIn()) void this.loadLists();
  }

  private async load() {
    try {
      this.data.set(await this.api.adblock());
    } catch (e) {
      this.message.set(errorMessage(e));
    }
  }

  private async loadLists() {
    const l = await this.api.adblockLists();
    this.lists.set({ allow: l.allow.join('\n'), deny: l.deny.join('\n') });
  }

  protected count(n: number | null | undefined) {
    return n == null ? '—' : new Intl.NumberFormat().format(n);
  }

  protected minutesLeft(s: number) {
    return Math.max(1, Math.ceil(s / 60));
  }

  private async act(fn: () => Promise<unknown>, done: string) {
    this.busy.set(true);
    try {
      await fn();
      this.message.set(done);
    } catch (e) {
      this.message.set(errorMessage(e));
    } finally {
      this.busy.set(false);
      await this.load();
    }
  }

  protected setEnabled(on: boolean) {
    return this.act(() => this.api.setBlocking(on), on ? 'Ad blocking is on.' : 'Ad blocking is off (until you turn it back on).');
  }

  protected pause(minutes: number) {
    return this.act(() => this.api.setBlocking(false, minutes), `Ad blocking paused for ${minutes} minutes.`);
  }

  protected setList(kind: ListKind, text: string) {
    this.lists.update((l) => ({ ...l, [kind]: text }));
  }

  protected allow(domain: string) {
    const current = this.lists().allow.split('\n').map((d) => d.trim()).filter(Boolean);
    if (!current.includes(domain)) this.setList('allow', [...current, domain].join('\n'));
    return this.saveList('allow');
  }

  protected async saveList(kind: ListKind) {
    const domains = this.lists()[kind].split(/\s+/).map((d) => d.trim()).filter(Boolean);
    this.listErrors.set({});
    this.busy.set(true);
    try {
      const r = await this.api.saveAdblockList(kind, domains);
      this.setList(kind, r.domains.join('\n'));
      this.message.set(r.refreshed ? `${kind === 'allow' ? 'Allowlist' : 'Denylist'} saved and applied.`
                                   : 'Saved — it applies when the ad blocker restarts.');
    } catch (e) {
      if (e instanceof HttpErrorResponse && e.status === 422) {
        this.listErrors.set({ [kind]: `Not valid domains: ${(e.error.invalid ?? []).join(', ')}` });
        document.getElementById(kind + '-list')?.focus();
      } else {
        this.message.set(errorMessage(e));
      }
    } finally {
      this.busy.set(false);
      await this.load();
    }
  }
}
