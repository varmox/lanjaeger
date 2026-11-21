import { Component, inject, signal, viewChild } from '@angular/core';
import { ClrDatagridModule } from '@clr/angular/data/datagrid';
import { ClrIcon } from '@clr/angular/icon';

import { Api, Job, Service, errorMessage } from '../shared/api';
import { ConfirmDialog } from '../shared/confirm-dialog';
import { poll } from '../shared/poll';
import { JobPanel } from '../shared/job-panel';

@Component({
  selector: 'lj-services',
  imports: [ClrDatagridModule, ClrIcon, ConfirmDialog, JobPanel],
  template: `
    <h1>Services</h1>
    @if (!available()) {
      <div class="alert alert-warning" role="alert">
        <div class="alert-items"><div class="alert-item static"><span class="alert-text">
          Docker isn't reachable from the web UI — mount <code>/var/run/docker.sock</code> to use controls.
        </span></div></div>
      </div>
    }

    <p class="lj-status" role="status">{{ message() }}</p>

    <clr-datagrid>
      <clr-dg-column>Service</clr-dg-column>
      <clr-dg-column>State</clr-dg-column>
      <clr-dg-column>Health</clr-dg-column>
      <clr-dg-column>Image</clr-dg-column>
      <clr-dg-column>Actions</clr-dg-column>
      <clr-dg-row *clrDgItems="let s of services()" [clrDgItem]="s">
        <clr-dg-cell><strong>{{ s.service }}</strong></clr-dg-cell>
        <clr-dg-cell>{{ s.status }}</clr-dg-cell>
        <clr-dg-cell>
          <span class="label" [class.label-success]="s.health === 'healthy'" [class.label-danger]="s.health === 'unhealthy'"
                [class.label-warning]="s.health === 'starting'">{{ s.health ?? 'n/a' }}</span>
        </clr-dg-cell>
        <clr-dg-cell><code>{{ s.image }}</code></clr-dg-cell>
        <clr-dg-cell>
          @if (managed().includes(s.service)) {
            <button type="button" class="btn btn-sm btn-outline" (click)="restart(s)" [disabled]="busy()">
              <cds-icon shape="refresh" aria-hidden="true"></cds-icon> Restart
            </button>
            <button type="button" class="btn btn-sm btn-link" (click)="showLogs(s)">Logs</button>
          } @else {
            <span class="lj-muted">managed by update.sh</span>
          }
        </clr-dg-cell>
      </clr-dg-row>
      <clr-dg-placeholder>No containers found.</clr-dg-placeholder>
    </clr-datagrid>

    <section class="lj-section" aria-labelledby="update-h">
      <h2 id="update-h">Update</h2>
      <p>Pulls the newest lancache images and recreates the containers that changed. Downloads in progress are interrupted.</p>
      <button type="button" class="btn btn-primary" (click)="update()" [disabled]="busy()">
        <cds-icon shape="download" aria-hidden="true"></cds-icon> Update images
      </button>
      <lj-job-panel [job]="job()" />
    </section>

    @if (logs(); as l) {
      <section class="lj-section" aria-labelledby="logs-h">
        <h2 id="logs-h">Logs: {{ l.service }}</h2>
        <pre class="lj-pre" tabindex="0">{{ l.text || '(empty)' }}</pre>
      </section>
    }

    <lj-confirm-dialog #confirmRestart [heading]="'Restart ' + (pending()?.service ?? 'service') + '?'" confirmLabel="Restart" [danger]="true">
      Clients downloading right now lose their connection for a few seconds and resume automatically.
    </lj-confirm-dialog>
    <lj-confirm-dialog #confirmUpdate heading="Update all lancache images?" confirmLabel="Update">
      This pulls new images and recreates DNS, cache and log rotation. It takes a minute; active downloads are interrupted.
    </lj-confirm-dialog>
  `,
})
export class ServicesPage {
  private readonly api = inject(Api);
  protected readonly services = signal<Service[]>([]);
  protected readonly managed = signal<string[]>([]);
  protected readonly available = signal(true);
  protected readonly busy = signal(false);
  protected readonly message = signal('');
  protected readonly pending = signal<Service | null>(null);
  protected readonly logs = signal<{ service: string; text: string } | null>(null);
  protected readonly job = signal<Job | null>(null);

  private readonly confirmRestart = viewChild.required<ConfirmDialog>('confirmRestart');
  private readonly confirmUpdate = viewChild.required<ConfirmDialog>('confirmUpdate');

  constructor() {
    poll(() => this.load(), 5000);
  }

  private async load() {
    const [svc, job] = await Promise.all([this.api.services(), this.api.job()]);
    this.available.set(svc.available);
    this.services.set(svc.services);
    this.managed.set(svc.managed ?? []);
    this.job.set(job.job);
    this.busy.set(job.job?.state === 'running');
  }

  protected async restart(s: Service) {
    this.pending.set(s);
    if (!(await this.confirmRestart().ask())) return;
    this.busy.set(true);
    this.message.set(`Restarting ${s.service}…`);
    try {
      await this.api.restart(s.service);
      this.message.set(`${s.service} restarted.`);
    } catch (e) {
      this.message.set(`Restart failed: ${errorMessage(e)}`);
    } finally {
      this.busy.set(false);
      await this.load();
    }
  }

  protected async showLogs(s: Service) {
    try {
      const r = await this.api.logs(s.service);
      this.logs.set({ service: s.service, text: r.text });
    } catch (e) {
      this.message.set(`Couldn't load logs: ${errorMessage(e)}`);
    }
  }

  protected async update() {
    if (!(await this.confirmUpdate().ask())) return;
    try {
      this.job.set(await this.api.startJob('update'));
      this.busy.set(true);
      this.message.set('Update started.');
    } catch (e) {
      this.message.set(errorMessage(e));
    }
  }
}
