import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ClrIcon } from '@clr/angular/icon';

import { AlertSettings, Alerts, Api, SECRET_PLACEHOLDER, errorMessage } from '../shared/api';
import { AgoPipe } from '../shared/format';
import { poll } from '../shared/poll';

@Component({
  selector: 'lj-alerts',
  imports: [FormsModule, ClrIcon, AgoPipe],
  template: `
    <h1>Alerts</h1>
    <p>Get a message when the cache disk fills up, a service goes down, a prefill fails or the ad blocklist is empty —
       once when it starts, again every few hours while it lasts, and when it's resolved.</p>

    <p class="lj-status" role="status">{{ message() }}</p>

    @if (data(); as d) {
      <section class="lj-section" aria-labelledby="active-h">
        <h2 id="active-h">Active now</h2>
        @for (a of d.active; track a.key) {
          <div class="alert" [class.alert-danger]="a.severity === 'critical'" [class.alert-warning]="a.severity !== 'critical'">
            <div class="alert-items"><div class="alert-item static">
              <span class="alert-text"><strong>{{ a.title }}</strong> — since {{ a.firedAt | ago }}<br>{{ a.body }}</span>
            </div></div>
          </div>
        } @empty {
          <p><cds-icon shape="check-circle" status="success" aria-hidden="true"></cds-icon> Everything looks fine.</p>
        }
      </section>

      <form class="clr-form clr-form-vertical" (ngSubmit)="save()">
        <section class="card lj-card-section" aria-labelledby="channels-h">
          <div class="card-block">
            <h2 id="channels-h" class="card-title">Where to send alerts</h2>
            <div class="clr-toggle-wrapper">
              <input type="checkbox" role="switch" id="alerts-on" class="clr-toggle" [(ngModel)]="s.enabled" name="enabled">
              <label for="alerts-on">Send alerts</label>
            </div>

            <fieldset class="lj-fieldset">
              <legend>ntfy (phone push notifications)</legend>
              <div class="clr-checkbox-wrapper">
                <input type="checkbox" id="ntfy-on" class="clr-checkbox" [(ngModel)]="s.channels.ntfy.enabled" name="ntfyOn">
                <label for="ntfy-on">Use ntfy</label>
              </div>
              <label for="ntfy-url">Topic URL</label>
              <input id="ntfy-url" name="ntfyUrl" type="url" class="clr-input lj-wide" [(ngModel)]="s.channels.ntfy.url"
                     placeholder="https://ntfy.sh/my-lancache-x7k2" autocomplete="off" spellcheck="false" aria-describedby="ntfy-help">
              <p id="ntfy-help" class="lj-muted">Install the ntfy app and subscribe to the same topic. Use a hard-to-guess topic name on ntfy.sh.</p>
              <label for="ntfy-token">Access token (optional)</label>
              <input id="ntfy-token" name="ntfyToken" type="password" class="clr-input lj-wide" autocomplete="off"
                     [(ngModel)]="s.channels.ntfy.token" [placeholder]="placeholderFor(s.channels.ntfy.token)">
            </fieldset>

            <fieldset class="lj-fieldset">
              <legend>Discord</legend>
              <div class="clr-checkbox-wrapper">
                <input type="checkbox" id="discord-on" class="clr-checkbox" [(ngModel)]="s.channels.discord.enabled" name="discordOn">
                <label for="discord-on">Use Discord</label>
              </div>
              <label for="discord-webhook">Webhook URL</label>
              <input id="discord-webhook" name="discordWebhook" type="password" class="clr-input lj-wide" autocomplete="off"
                     [(ngModel)]="s.channels.discord.webhook" [placeholder]="placeholderFor(s.channels.discord.webhook, 'https://discord.com/api/webhooks/…')"
                     aria-describedby="discord-help">
              <p id="discord-help" class="lj-muted">Channel settings → Integrations → Webhooks → New webhook → Copy URL.</p>
            </fieldset>

            <fieldset class="lj-fieldset">
              <legend>Email</legend>
              <div class="clr-checkbox-wrapper">
                <input type="checkbox" id="email-on" class="clr-checkbox" [(ngModel)]="s.channels.email.enabled" name="emailOn">
                <label for="email-on">Use email</label>
              </div>
              <div class="lj-grid-2">
                <div><label for="smtp-host">SMTP server</label>
                  <input id="smtp-host" name="smtpHost" class="clr-input" [(ngModel)]="s.channels.email.host" autocomplete="off" spellcheck="false"></div>
                <div><label for="smtp-port">Port</label>
                  <input id="smtp-port" name="smtpPort" type="number" inputmode="numeric" class="clr-input" [(ngModel)]="s.channels.email.port"></div>
                <div><label for="smtp-sec">Encryption</label>
                  <select id="smtp-sec" name="smtpSec" class="clr-select" [(ngModel)]="s.channels.email.security">
                    <option value="starttls">STARTTLS (port 587)</option>
                    <option value="ssl">SSL/TLS (port 465)</option>
                    <option value="none">None</option>
                  </select></div>
                <div><label for="smtp-user">Username</label>
                  <input id="smtp-user" name="smtpUser" class="clr-input" [(ngModel)]="s.channels.email.username" autocomplete="off" spellcheck="false"></div>
                <div><label for="smtp-pass">Password</label>
                  <input id="smtp-pass" name="smtpPass" type="password" class="clr-input" autocomplete="new-password"
                         [(ngModel)]="s.channels.email.password" [placeholder]="placeholderFor(s.channels.email.password)"></div>
                <div><label for="smtp-from">From</label>
                  <input id="smtp-from" name="smtpFrom" type="email" class="clr-input" [(ngModel)]="s.channels.email.from" autocomplete="off"></div>
                <div><label for="smtp-to">To (comma-separated)</label>
                  <input id="smtp-to" name="smtpTo" type="text" inputmode="email" class="clr-input" [(ngModel)]="s.channels.email.to" autocomplete="off"></div>
              </div>
            </fieldset>
          </div>
        </section>

        <section class="card lj-card-section" aria-labelledby="rules-h">
          <div class="card-block">
            <h2 id="rules-h" class="card-title">When to alert</h2>
            <div class="lj-inline-field">
              <label for="disk-pct">Cache disk fuller than</label>
              <input id="disk-pct" name="diskPct" type="number" min="50" max="99" class="clr-input" [(ngModel)]="s.rules.diskPercent">
              <span>% or less than</span>
              <input id="disk-free" name="diskFree" type="number" min="0" class="clr-input" aria-label="Minimum free space in GB"
                     [(ngModel)]="s.rules.diskFreeGb">
              <span>GB free</span>
            </div>
            <div class="clr-checkbox-wrapper">
              <input type="checkbox" id="svc-down" class="clr-checkbox" [(ngModel)]="s.rules.serviceDown" name="svcDown">
              <label for="svc-down">A service (DNS, ad blocker, cache, log rotation) is stopped or unhealthy</label>
            </div>
            <div class="lj-inline-field lj-indent">
              <label for="svc-min">for at least</label>
              <input id="svc-min" name="svcMin" type="number" min="1" max="1440" class="clr-input" [(ngModel)]="s.rules.serviceDownMinutes">
              <span>minutes</span>
            </div>
            <div class="clr-checkbox-wrapper">
              <input type="checkbox" id="pf-fail" class="clr-checkbox" [(ngModel)]="s.rules.prefillFailed" name="pfFail">
              <label for="pf-fail">A prefill run fails</label>
            </div>
            <div class="clr-checkbox-wrapper">
              <input type="checkbox" id="ab-empty" class="clr-checkbox" [(ngModel)]="s.rules.adblockEmpty" name="abEmpty">
              <label for="ab-empty">The ad blocklist is empty (download failed)</label>
            </div>
            <div class="lj-inline-field">
              <label for="repeat">Remind every</label>
              <input id="repeat" name="repeat" type="number" min="1" max="168" class="clr-input" [(ngModel)]="s.repeatHours">
              <span>hours while a problem lasts</span>
            </div>
          </div>
        </section>

        <div class="btn-row lj-actions">
          <button type="submit" class="btn btn-primary" [disabled]="busy()">Save</button>
          <button type="button" class="btn btn-outline" (click)="test()" [disabled]="busy()">Send test alert</button>
        </div>
        @if (testResults(); as r) {
          <ul class="lj-test-results" aria-label="Test results">
            @for (k of keys(r); track k) {
              <li>
                <cds-icon [shape]="r[k] === 'ok' ? 'check-circle' : 'times-circle'" [status]="r[k] === 'ok' ? 'success' : 'danger'" aria-hidden="true"></cds-icon>
                <strong>{{ k }}</strong>: {{ r[k] === 'ok' ? 'sent' : r[k] }}
              </li>
            }
          </ul>
        }
      </form>

      <section class="lj-section" aria-labelledby="history-h">
        <h2 id="history-h">History</h2>
        @if (d.history.length) {
          <table class="table table-compact">
            <caption class="lj-visually-hidden">Recent alerts</caption>
            <thead><tr><th scope="col">When</th><th scope="col">Alert</th><th scope="col">Delivered</th></tr></thead>
            <tbody>
              @for (h of d.history; track h.time + h.key + h.kind) {
                <tr>
                  <td>{{ h.time | ago }}</td>
                  <td class="left">
                    <span class="label" [class.label-danger]="h.severity === 'critical'" [class.label-warning]="h.severity === 'warning'"
                          [class.label-success]="h.severity === 'resolved'">{{ h.kind }}</span> {{ h.title }}
                  </td>
                  <td class="left">{{ delivered(h.results) }}</td>
                </tr>
              }
            </tbody>
          </table>
        } @else {
          <p class="lj-muted">No alerts yet.</p>
        }
      </section>
    } @else {
      <span class="spinner spinner-inline">Loading…</span> Loading…
    }
  `,
})
export class AlertsPage {
  private readonly api = inject(Api);
  protected readonly data = signal<Alerts | null>(null);
  protected readonly busy = signal(false);
  protected readonly message = signal('');
  protected readonly testResults = signal<Record<string, string> | null>(null);
  protected s!: AlertSettings;
  private loaded = false;

  constructor() {
    poll(() => this.load(), 30_000);
  }

  private async load() {
    try {
      const d = await this.api.alerts();
      if (!this.loaded) {
        this.s = structuredClone(d.settings);
        this.loaded = true;
      }
      this.data.set(d);
    } catch (e) {
      this.message.set(errorMessage(e));
    }
  }

  /** Stored secrets come back as a placeholder; show that something is set. */
  protected placeholderFor(v: string, fallback = '') {
    return v === SECRET_PLACEHOLDER ? '•••••••• (saved — type to replace)' : fallback;
  }

  protected keys(r: Record<string, string>) {
    return Object.keys(r);
  }

  protected delivered(r: Record<string, string>) {
    const k = Object.keys(r);
    if (!k.length) return 'not sent (alerts off)';
    return k.map((c) => `${c}: ${r[c] === 'ok' ? '✓' : 'failed'}`).join(', ');
  }

  protected async save() {
    this.busy.set(true);
    try {
      this.s = (await this.api.saveAlerts(this.s)).settings;
      this.message.set('Saved.');
    } catch (e) {
      this.message.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async test() {
    this.busy.set(true);
    this.testResults.set(null);
    try {
      this.testResults.set((await this.api.testAlerts()).results);
      this.message.set('Test sent — results below.');
    } catch (e) {
      this.message.set(`${errorMessage(e)} (save your settings first)`);
    } finally {
      this.busy.set(false);
    }
  }
}
