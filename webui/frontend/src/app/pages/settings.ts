import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ConfigField, Job, errorMessage } from '../shared/api';
import { ConfirmDialog } from '../shared/confirm-dialog';
import { JobPanel } from '../shared/job-panel';
import { poll } from '../shared/poll';

const GROUPS: { id: ConfigField['group']; title: string; note?: string }[] = [
  { id: 'network', title: 'Network', note: 'A wrong IP here can make the cache or DNS unreachable — double-check before applying.' },
  { id: 'storage', title: 'Storage' },
  { id: 'logs', title: 'Log rotation' },
  { id: 'operation', title: 'Operation' },
];

@Component({
  selector: 'lj-settings',
  imports: [FormsModule, ConfirmDialog, JobPanel],
  template: `
    <h1>Settings</h1>
    <p>These values are written to <code>.env</code>. <code>CACHE_ROOT</code>, images and the web UI password can only be changed in the file.</p>

    @for (w of warnings(); track w) {
      <div class="alert alert-warning" role="alert">
        <div class="alert-items"><div class="alert-item static"><span class="alert-text">{{ w }}</span></div></div>
      </div>
    }

    @if (fields().length) {
      <form class="clr-form clr-form-horizontal" (ngSubmit)="save()" novalidate>
        @for (g of groups; track g.id) {
          <fieldset class="lj-fieldset">
            <legend>{{ g.title }}</legend>
            @if (g.note) { <p class="lj-muted">{{ g.note }}</p> }
            @for (f of byGroup()[g.id]; track f.key) {
              <div class="clr-form-control" [class.clr-error]="errors()[f.key]">
                <label class="clr-control-label" [for]="f.key">{{ f.key }}</label>
                <div class="clr-control-container">
                  <div class="clr-input-wrapper">
                    @if (f.options) {
                      <select class="clr-select" [id]="f.key" [name]="f.key" [(ngModel)]="values[f.key]"
                              [attr.aria-describedby]="f.key + '-help ' + f.key + '-err'"
                              [attr.aria-invalid]="errors()[f.key] ? 'true' : null">
                        @for (o of f.options; track o) { <option [value]="o">{{ o }}</option> }
                      </select>
                    } @else {
                      <input class="clr-input lj-wide" type="text" [id]="f.key" [name]="f.key" [(ngModel)]="values[f.key]"
                             autocomplete="off" spellcheck="false" autocapitalize="off"
                             [attr.aria-describedby]="f.key + '-help ' + f.key + '-err'"
                             [attr.aria-invalid]="errors()[f.key] ? 'true' : null">
                    }
                  </div>
                  <span class="clr-subtext" [id]="f.key + '-help'">{{ f.help }}</span>
                  <span class="clr-subtext lj-field-error" [id]="f.key + '-err'">{{ errors()[f.key] }}</span>
                </div>
              </div>
            }
          </fieldset>
        }

        <p class="lj-status" role="status">{{ message() }}</p>
        <button type="submit" class="btn btn-primary" [disabled]="busy()">Save</button>
      </form>

      <lj-job-panel [job]="job()" />
    } @else {
      <p role="status">{{ message() || 'Loading…' }}</p>
    }

    <lj-confirm-dialog #confirmApply heading="Apply the new settings now?" confirmLabel="Apply now">
      Saved. The containers are recreated with the new values, which interrupts active downloads for a few seconds.
      Choose Cancel to apply later (with <code>docker compose up -d</code> or the next update).
    </lj-confirm-dialog>
  `,
})
export class SettingsPage {
  private readonly api = inject(Api);
  protected readonly groups = GROUPS;
  protected readonly fields = signal<ConfigField[]>([]);
  protected values: Record<string, string> = {};
  protected readonly errors = signal<Record<string, string>>({});
  protected readonly message = signal('');
  protected readonly warnings = signal<string[]>([]);
  protected readonly busy = signal(false);
  protected readonly job = signal<Job | null>(null);
  private readonly confirmApply = viewChild.required<ConfirmDialog>('confirmApply');

  protected readonly byGroup = computed(() => {
    const out: Record<string, ConfigField[]> = {};
    for (const f of this.fields()) (out[f.group] ??= []).push(f);
    return out;
  });

  constructor() {
    void this.load();
    poll(async () => {
      const j = (await this.api.job()).job;
      this.job.set(j && j.action === 'apply' ? j : this.job());
    }, 3000);
  }

  private async load() {
    try {
      const { fields, warnings } = await this.api.config();
      this.fields.set(fields);
      this.warnings.set(warnings ?? []);
      this.values = Object.fromEntries(fields.map((f) => [f.key, f.value]));
    } catch (e) {
      this.message.set(errorMessage(e));
    }
  }

  protected async save() {
    this.busy.set(true);
    this.errors.set({});
    this.message.set('');
    try {
      const { changed, warnings } = await this.api.saveConfig(this.values);
      this.warnings.set(warnings ?? []);
      if (!changed.length) {
        this.message.set('Nothing changed.');
        return;
      }
      this.message.set(`Saved: ${changed.join(', ')}.`);
      if (await this.confirmApply().ask()) {
        this.job.set(await this.api.startJob('apply'));
      }
      await this.load();
    } catch (e) {
      if (e instanceof HttpErrorResponse && e.status === 422) {
        this.errors.set(e.error.fields ?? {});
        const n = Object.keys(this.errors()).length;
        this.message.set(`${n} ${n === 1 ? 'value needs' : 'values need'} fixing — nothing was saved.`);
        // Move focus to the first invalid field so keyboard users land on it.
        const first = this.fields().find((f) => this.errors()[f.key]);
        if (first) document.getElementById(first.key)?.focus();
      } else {
        this.message.set(errorMessage(e));
      }
    } finally {
      this.busy.set(false);
    }
  }
}
