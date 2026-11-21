import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ClrIcon } from '@clr/angular/icon';

import { Api, DnsEntry, DnsLookup, errorMessage } from '../shared/api';
import { ConfirmDialog } from '../shared/confirm-dialog';

@Component({
  selector: 'lj-dns',
  imports: [FormsModule, ClrIcon, ConfirmDialog],
  template: `
    <h1>DNS entries</h1>
    <p>Your own names on the LAN — for example <code>nas.lan</code> or a game server. Every device using this DNS
       resolves them. Changes apply within a minute, without interrupting DNS.</p>

    <p class="lj-status" role="status">{{ message() }}</p>

    <form (ngSubmit)="save()">
      <div class="lj-table-scroll">
      <table class="table lj-dns-table">
        <caption class="lj-visually-hidden">Custom DNS entries</caption>
        <thead>
          <tr>
            <th scope="col" class="left">Name</th>
            <th scope="col" class="left">IP address</th>
            <th scope="col" class="left">Note</th>
            <th scope="col"><span class="lj-visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          @for (e of entries(); track $index; let i = $index) {
            <tr [class.lj-row-error]="rowErrors()[i]">
              <td class="left">
                <input class="clr-input" [name]="'name' + i" [(ngModel)]="e.name" placeholder="nas.lan"
                       autocomplete="off" spellcheck="false" autocapitalize="off" [attr.aria-label]="'Name, row ' + (i + 1)"
                       [attr.aria-invalid]="rowErrors()[i] ? 'true' : null" [attr.aria-describedby]="'err' + i">
              </td>
              <td class="left">
                <input class="clr-input" [name]="'ip' + i" [(ngModel)]="e.ip" placeholder="192.168.1.20"
                       autocomplete="off" spellcheck="false" inputmode="decimal" [attr.aria-label]="'IP address, row ' + (i + 1)"
                       [attr.aria-invalid]="rowErrors()[i] ? 'true' : null" [attr.aria-describedby]="'err' + i">
              </td>
              <td class="left">
                <input class="clr-input" [name]="'note' + i" [(ngModel)]="e.note" maxlength="100"
                       autocomplete="off" [attr.aria-label]="'Note, row ' + (i + 1)">
              </td>
              <td>
                <button type="button" class="btn btn-sm btn-link" (click)="remove(i)" [attr.aria-label]="'Remove ' + (e.name || 'row ' + (i + 1))">
                  <cds-icon shape="trash" aria-hidden="true"></cds-icon> Remove
                </button>
              </td>
            </tr>
            @if (rowErrors()[i]) {
              <tr class="lj-row-error-msg"><td colspan="4" class="left"><span [id]="'err' + i" class="lj-error">{{ rowErrors()[i] }}</span></td></tr>
            }
          } @empty {
            <tr><td colspan="4" class="left lj-muted">No entries yet.</td></tr>
          }
        </tbody>
      </table>
      </div>

      <div class="lj-actions">
        <button type="button" class="btn btn-outline" (click)="add()"><cds-icon shape="plus" aria-hidden="true"></cds-icon> Add entry</button>
        <button type="submit" class="btn btn-primary" [disabled]="busy()">Save</button>
        <button type="button" class="btn btn-link" (click)="applyNow()" [disabled]="busy()">Apply now</button>
      </div>
      <p class="lj-muted">Exact names only — <code>nas.lan</code> doesn't cover <code>www.nas.lan</code>. Avoid <code>.local</code>
         (used by Bonjour/mDNS). An entry for a game download domain takes priority over the cache for that name.</p>
    </form>

    <section class="lj-section" aria-labelledby="lookup-h">
      <h2 id="lookup-h">Test a name</h2>
      <form class="lj-inline-field" (ngSubmit)="lookup()">
        <label for="lookup-name">Name</label>
        <input id="lookup-name" name="lookupName" class="clr-input" [(ngModel)]="lookupName" placeholder="nas.lan"
               autocomplete="off" spellcheck="false" autocapitalize="off" enterkeyhint="search">
        <label for="lookup-type">Type</label>
        <select id="lookup-type" name="lookupType" class="clr-select" [(ngModel)]="lookupType">
          @for (t of types; track t) { <option [value]="t">{{ t }}</option> }
        </select>
        <button type="submit" class="btn btn-sm btn-outline"><cds-icon shape="search" aria-hidden="true"></cds-icon> Look up</button>
      </form>
      @if (result(); as r) {
        <dl class="lj-lookup" aria-live="polite">
          <dt>Answer</dt><dd><code>{{ r.response || '(none)' }}</code></dd>
          <dt>Answered by</dt><dd>{{ source(r) }}</dd>
          <dt>Status</dt><dd>{{ r.returnCode }}</dd>
        </dl>
      }
    </section>

    <lj-confirm-dialog #confirmApply heading="Apply DNS entries now?" confirmLabel="Apply now">
      Entries apply automatically within a minute. Applying now restarts the DNS server: every device loses DNS
      for about two seconds, and the 24-hour ad-blocking statistics start over.
    </lj-confirm-dialog>
  `,
})
export class DnsPage {
  private readonly api = inject(Api);
  protected readonly entries = signal<DnsEntry[]>([]);
  protected readonly rowErrors = signal<Record<number, string>>({});
  protected readonly busy = signal(false);
  protected readonly message = signal('');
  protected readonly result = signal<DnsLookup | null>(null);
  protected readonly types = ['A', 'AAAA', 'CNAME', 'MX', 'TXT'];
  protected lookupName = '';
  protected lookupType = 'A';
  private readonly confirmApply = viewChild.required<ConfirmDialog>('confirmApply');

  constructor() {
    void this.load();
  }

  private async load() {
    try {
      this.entries.set((await this.api.dnsEntries()).entries);
    } catch (e) {
      this.message.set(errorMessage(e));
    }
  }

  protected add() {
    this.entries.update((list) => [...list, { name: '', ip: '', note: '' }]);
    // Move focus to the new row's name field.
    queueMicrotask(() => document.getElementsByName('name' + (this.entries().length - 1))[0]?.focus());
  }

  protected remove(i: number) {
    const name = this.entries()[i].name;
    this.entries.update((list) => list.filter((_, j) => j !== i));
    this.rowErrors.set({});
    this.message.set(`${name || 'Entry'} removed — save to keep the change.`);
  }

  protected async save() {
    this.busy.set(true);
    this.rowErrors.set({});
    try {
      const nonEmpty = this.entries().filter((e) => e.name.trim() || e.ip.trim());
      this.entries.set((await this.api.saveDnsEntries(nonEmpty)).entries);
      this.message.set('Saved — devices see the change within a minute.');
    } catch (e) {
      if (e instanceof HttpErrorResponse && e.status === 422) {
        const rows: Record<string, string> = e.error.rows ?? {};
        this.rowErrors.set(Object.fromEntries(Object.entries(rows).map(([k, v]) => [Number(k), v])));
        const n = Object.keys(rows).length;
        this.message.set(`${n} ${n === 1 ? 'entry needs' : 'entries need'} fixing — nothing was saved.`);
        const first = Math.min(...Object.keys(rows).map(Number));
        document.getElementsByName('name' + first)[0]?.focus();
      } else {
        this.message.set(errorMessage(e));
      }
    } finally {
      this.busy.set(false);
    }
  }

  protected async applyNow() {
    if (!(await this.confirmApply().ask())) return;
    this.busy.set(true);
    try {
      await this.api.applyDnsNow();
      this.message.set('DNS restarted — saved entries are active now.');
    } catch (e) {
      this.message.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async lookup() {
    if (!this.lookupName.trim()) return;
    try {
      this.result.set(await this.api.dnsLookup(this.lookupName.trim(), this.lookupType));
    } catch (e) {
      this.result.set(null);
      this.message.set(errorMessage(e));
    }
  }

  protected source(r: DnsLookup) {
    const t = r.responseType;
    if (t === 'HOSTSFILE') return 'Your DNS entries';
    if (t === 'BLOCKED') return 'Ad blocker (blocked)';
    if (t === 'CACHED') return 'DNS cache';
    if (t === 'RESOLVED') return 'lancache-dns / upstream DNS';
    return `${t} — ${r.reason}`;
  }
}
