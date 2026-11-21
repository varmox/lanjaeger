import { Component, inject, signal } from '@angular/core';
import { ClrIcon } from '@clr/angular/icon';

import { Api, CheckResult, errorMessage } from '../shared/api';

type Verdict = 'ok' | 'partial' | 'none';

/**
 * Public "Is my device using the cache?" page. The browser looks up a few
 * one-time names that only lanjaeger's DNS knows; the server reports how
 * many of those lookups actually reached it.
 */
@Component({
  selector: 'lj-check',
  imports: [ClrIcon],
  template: `
    <div class="lj-check">
      <h1>Is my device using the cache?</h1>

      @switch (state()) {
        @case ('running') {
          <p role="status"><span class="spinner spinner-inline">Checking</span> Checking this device's DNS…</p>
        }
        @case ('error') {
          <div class="alert alert-danger" role="alert"><div class="alert-items"><div class="alert-item static">
            <span class="alert-text">The check couldn't run: {{ error() }}</span></div></div></div>
        }
        @case ('done') {
          @if (result(); as r) {
            <div [class]="'lj-verdict lj-' + verdict()" role="status">
              <cds-icon [shape]="verdict() === 'ok' ? 'check-circle' : verdict() === 'partial' ? 'exclamation-triangle' : 'times-circle'"
                        [status]="verdict() === 'ok' ? 'success' : verdict() === 'partial' ? 'warning' : 'danger'"
                        size="xl" aria-hidden="true"></cds-icon>
              <div>
                @switch (verdict()) {
                  @case ('ok') {
                    <h2>Yes — downloads on this device go through the cache.</h2>
                    <p>All {{ r.total }} test lookups reached the LAN's DNS server. Nothing to do.</p>
                  }
                  @case ('partial') {
                    <h2>Partly — this device also uses another DNS server.</h2>
                    <p>Only {{ r.seen }} of {{ r.total }} test lookups reached the LAN's DNS. The rest went elsewhere, so some
                       downloads will skip the cache (and the ad blocker).</p>
                  }
                  @case ('none') {
                    <h2>No — this device isn't using the LAN's DNS.</h2>
                    <p>None of the {{ r.total }} test lookups reached the LAN's DNS server, so downloads go straight to the internet.</p>
                  }
                }
              </div>
            </div>

            @if (verdict() !== 'ok') {
              <section class="lj-section" aria-labelledby="fix-h">
                <h2 id="fix-h">How to fix it</h2>
                <ol class="lj-fix">
                  @if (verdict() === 'partial') {
                    <li><strong>IPv6 DNS from the router</strong> is the usual cause. Ask the organiser — the network should hand out
                        only {{ r.dnsServer }}{{ r.dnsServer6 ? ' (IPv6: ' + r.dnsServer6 + ')' : '' }}. As a quick fix, turn off IPv6 on
                        this device's network adapter.</li>
                    <li><strong>A second DNS server set by hand</strong>, e.g. 8.8.8.8 next to the LAN's. Set DNS back to automatic.</li>
                  } @else {
                    <li><strong>Browser uses its own encrypted DNS?</strong> Chrome/Edge: Settings → Privacy → Use secure DNS → off.
                        Firefox: Settings → Privacy & Security → DNS over HTTPS → off. Then run the check again —
                        games and launchers may already be fine.</li>
                    <li><strong>DNS set by hand</strong> (e.g. 1.1.1.1 or 8.8.8.8) in the network adapter settings, a VPN, or iCloud
                        Private Relay. Set DNS to automatic, or to {{ r.dnsServer }}, and switch the VPN off.</li>
                    <li><strong>Connected to the wrong network?</strong> Use the LAN party's network, ideally by cable.</li>
                  }
                </ol>
                <p>After changing something, flush the DNS cache (Windows: <code>ipconfig /flushdns</code>) and
                  <button type="button" class="btn btn-sm btn-link lj-inline-btn" (click)="run()">run the check again</button>.</p>
              </section>
            }

            <p class="lj-muted">This device: {{ r.client }}{{ r.clientIsIpv6 ? ' (IPv6)' : '' }} · LAN DNS server: {{ r.dnsServer || 'unknown' }}</p>
          }
          @if (verdict() === 'ok') {
            <button type="button" class="btn btn-outline" (click)="run()">Check again</button>
          }
        }
      }
      <p class="lj-muted">Only the check's own test names are looked at — nothing else about this device or its browsing is recorded.</p>
    </div>
  `,
})
export class CheckPage {
  private readonly api = inject(Api);
  protected readonly state = signal<'running' | 'done' | 'error'>('running');
  protected readonly result = signal<CheckResult | null>(null);
  protected readonly verdict = signal<Verdict>('none');
  protected readonly error = signal('');

  constructor() {
    void this.run();
  }

  protected async run() {
    this.state.set('running');
    try {
      const { id, hosts } = await this.api.checkStart();
      const port = location.port ? `:${location.port}` : '';
      // Each fetch makes the browser resolve one test name; whether it
      // connects doesn't matter — the server counts the DNS lookups.
      await Promise.all(hosts.map((h) => probe(`http://${h}${port}/api/check/ping`)));
      await new Promise((r) => setTimeout(r, 300));
      const r = await this.api.checkResult(id);
      this.result.set(r);
      this.verdict.set(r.seen === r.total ? 'ok' : r.seen > 0 ? 'partial' : 'none');
      this.state.set('done');
    } catch (e) {
      this.error.set(errorMessage(e));
      this.state.set('error');
    }
  }
}

async function probe(url: string): Promise<void> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 4000);
  try {
    await fetch(url, { cache: 'no-store', signal: ctrl.signal });
  } catch {
    // Name didn't resolve or connect — expected when the device uses another DNS.
  } finally {
    clearTimeout(timer);
  }
}
