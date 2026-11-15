import { Component, inject, signal } from '@angular/core';
import { ClrDatagridModule } from '@clr/angular/data/datagrid';

import { Api, Counter } from '../shared/api';
import { AgoPipe, BytesPipe, percent } from '../shared/format';
import { poll } from '../shared/poll';

type ClientRow = Counter & { ip: string; total: number };

@Component({
  selector: 'lj-clients',
  imports: [ClrDatagridModule, BytesPipe, AgoPipe],
  template: `
    <h1>Clients</h1>
    <p>Every device that downloaded through the cache, with how much came from local disk.</p>
    <clr-datagrid [clrDgLoading]="loading()">
      <clr-dg-column [clrDgField]="'ip'">IP address</clr-dg-column>
      <clr-dg-column [clrDgField]="'total'" [clrDgSortOrder]="-1">Total</clr-dg-column>
      <clr-dg-column [clrDgField]="'hitBytes'">From cache</clr-dg-column>
      <clr-dg-column [clrDgField]="'missBytes'">From internet</clr-dg-column>
      <clr-dg-column>Hit rate</clr-dg-column>
      <clr-dg-column [clrDgField]="'lastSeen'">Last activity</clr-dg-column>
      <clr-dg-row *clrDgItems="let c of rows()" [clrDgItem]="c">
        <clr-dg-cell>{{ c.ip }}</clr-dg-cell>
        <clr-dg-cell>{{ c.total | bytes }}</clr-dg-cell>
        <clr-dg-cell>{{ c.hitBytes | bytes }}</clr-dg-cell>
        <clr-dg-cell>{{ c.missBytes | bytes }}</clr-dg-cell>
        <clr-dg-cell>{{ rate(c) }}</clr-dg-cell>
        <clr-dg-cell>{{ c.lastSeen | ago }}</clr-dg-cell>
      </clr-dg-row>
      <clr-dg-placeholder>No clients yet.</clr-dg-placeholder>
      <clr-dg-footer>
        <clr-dg-pagination #pg [clrDgPageSize]="25">
          <clr-dg-page-size [clrPageSizeOptions]="[25, 50, 100]">Clients per page</clr-dg-page-size>
          {{ pg.firstItem + 1 }}–{{ pg.lastItem + 1 }} of {{ pg.totalItems }} clients
        </clr-dg-pagination>
      </clr-dg-footer>
    </clr-datagrid>
  `,
})
export class ClientsPage {
  private readonly api = inject(Api);
  protected readonly rows = signal<ClientRow[]>([]);
  protected readonly loading = signal(true);

  constructor() {
    poll(async () => {
      try {
        const s = await this.api.stats();
        this.rows.set(s.clients.map((c) => ({ ...c, total: c.hitBytes + c.missBytes })));
      } finally {
        this.loading.set(false);
      }
    }, 10_000);
  }

  protected rate(c: ClientRow) {
    return percent(c.hitBytes, c.total);
  }
}
