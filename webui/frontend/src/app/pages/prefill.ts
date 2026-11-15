import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ClrIconModule } from '@clr/angular/icon';

import { Api, EpicLibrary, LoginService, Prefill, PrefillSettings, SteamGame, errorMessage } from '../shared/api';
import { LoginRelay } from '../shared/login-relay';
import { poll } from '../shared/poll';

@Component({
  selector: 'lj-prefill',
  imports: [FormsModule, ClrIconModule, LoginRelay],
  template: `
    <h1>Prefill</h1>
    <p>Download games into the cache ahead of time — for example the night before a LAN party — so even the first
       install on every machine runs at LAN speed. Already-prefilled games are only downloaded again when they update.</p>

    <p class="lj-status" role="status">{{ message() }}</p>

    @if (data(); as d) {
      <!-- Run / schedule -->
      <section class="card lj-card-section" aria-labelledby="run-h">
        <div class="card-block">
          <h2 id="run-h" class="card-title">Run</h2>
          @if (d.status.running) {
            <p><span class="spinner spinner-inline">Running</span>
              Prefilling <strong>{{ label(d.status.running) }}</strong>
              @if (d.status.queued.length) { — then {{ queuedLabels() }} }
            </p>
            <button type="button" class="btn btn-danger-outline" (click)="stop()" [disabled]="busy()">Stop</button>
          } @else {
            <button type="button" class="btn btn-primary" (click)="run()" [disabled]="busy() || !d.runnable.length">
              <cds-icon shape="download" aria-hidden="true"></cds-icon> Prefill now
            </button>
            @if (!d.runnable.length) {
              <span class="lj-muted">Enable a service below, choose games, log in where needed, then save.</span>
            }
          }

          <div class="lj-schedule">
            <div class="clr-toggle-wrapper">
              <input type="checkbox" role="switch" id="schedule-on" class="clr-toggle" [(ngModel)]="settings.schedule.enabled" name="schedOn">
              <label for="schedule-on">Prefill every night at</label>
            </div>
            <input type="time" class="clr-input" id="schedule-time" aria-label="Prefill time" [(ngModel)]="settings.schedule.time" name="schedTime">
            <button type="button" class="btn btn-sm btn-outline" (click)="save()" [disabled]="busy()">Save schedule</button>
          </div>
        </div>
      </section>

      <!-- Steam -->
      <section class="card lj-card-section" aria-labelledby="steam-h">
        <div class="card-block">
          <h2 id="steam-h" class="card-title">Steam</h2>

          @if (!d.loggedIn.steam) {
            <p>Steam only lets owners download games, so prefill needs your Steam login once. Your password is used for this
               login only and never stored — Steam gives back a token that later runs use.</p>
            <lj-login-relay service="steam" label="Steam" startLabel="Log in to Steam" (finished)="reload()">
              <p class="lj-muted">Answer the prompts above: username, password, then the Steam Guard code from your app or email.</p>
            </lj-login-relay>
          } @else {
            <p><cds-icon shape="check-circle" status="success" aria-hidden="true"></cds-icon> Logged in to Steam.
              <button type="button" class="btn btn-sm btn-link" (click)="logout('steam')">Log out</button></p>
          }

          <form class="clr-form clr-form-vertical" (ngSubmit)="save()">
            <div class="clr-toggle-wrapper">
              <input type="checkbox" role="switch" id="steam-on" class="clr-toggle" [(ngModel)]="settings.steam.enabled" name="steamOn">
              <label for="steam-on">Prefill Steam games</label>
            </div>

            <fieldset class="lj-fieldset">
              <legend>Which games</legend>
              <div class="clr-checkbox-wrapper">
                <input type="checkbox" id="steam-all" class="clr-checkbox" [(ngModel)]="settings.steam.all" name="all">
                <label for="steam-all">My whole library</label>
              </div>
              <div class="clr-checkbox-wrapper">
                <input type="checkbox" id="steam-recent" class="clr-checkbox" [(ngModel)]="settings.steam.recent" name="recent">
                <label for="steam-recent">Games played in the last two weeks</label>
              </div>
              <div class="lj-inline-field">
                <label for="steam-top">Most popular games on Steam right now (owned ones only), top</label>
                <input type="number" id="steam-top" class="clr-input" min="0" max="100" inputmode="numeric"
                       [(ngModel)]="settings.steam.top" name="top">
              </div>
            </fieldset>

            <fieldset class="lj-fieldset">
              <legend>Specific games</legend>
              <label for="steam-search">Search the Steam store</label>
              <input type="search" id="steam-search" class="clr-input lj-wide" name="search" [(ngModel)]="query"
                     (ngModelChange)="search($event)" autocomplete="off" enterkeyhint="search"
                     placeholder="e.g. Counter-Strike 2" aria-describedby="search-help">
              <p id="search-help" class="lj-muted">Games you don't own are skipped during prefill.</p>
              @if (results().length) {
                <ul class="lj-results" aria-label="Search results">
                  @for (g of results(); track g.id) {
                    <li>
                      <img [src]="g.image" alt="" width="120" height="45" loading="lazy">
                      <span>{{ g.name }}</span>
                      <button type="button" class="btn btn-sm btn-outline" (click)="add(g)" [disabled]="selected(g.id)">
                        {{ selected(g.id) ? 'Added' : 'Add' }}
                      </button>
                    </li>
                  }
                </ul>
              }
              <h3 class="lj-subhead">Selected ({{ gameIds().length }})</h3>
              @if (gameIds().length) {
                <ul class="lj-selected">
                  @for (id of gameIds(); track id) {
                    <li>
                      <span>{{ settings.steam.games[id] }}</span>
                      <button type="button" class="btn btn-sm btn-link" (click)="remove(id)"
                              [attr.aria-label]="'Remove ' + settings.steam.games[id]">
                        <cds-icon shape="trash" aria-hidden="true"></cds-icon>
                      </button>
                    </li>
                  }
                </ul>
              } @else {
                <p class="lj-muted">None yet.</p>
              }
            </fieldset>

            <fieldset class="lj-fieldset">
              <legend>Platforms</legend>
              @for (os of ['windows', 'linux', 'macos']; track os) {
                <div class="clr-checkbox-wrapper clr-checkbox-inline">
                  <input type="checkbox" class="clr-checkbox" [id]="'os-' + os" [name]="'os-' + os"
                         [checked]="settings.steam.os.includes(os)" (change)="toggleOs(os, $any($event.target).checked)">
                  <label [for]="'os-' + os">{{ os === 'macos' ? 'macOS' : os[0].toUpperCase() + os.slice(1) }}</label>
                </div>
              }
            </fieldset>

            <button type="submit" class="btn btn-primary" [disabled]="busy()">Save Steam settings</button>
          </form>
        </div>
      </section>

      <!-- Epic Games -->
      <section class="card lj-card-section" aria-labelledby="epic-h">
        <div class="card-block">
          <h2 id="epic-h" class="card-title">Epic Games</h2>
          @if (!d.loggedIn.epic) {
            <p>Epic only lets owners download games, so prefill needs your Epic login once:</p>
            <ol>
              <li>Click <strong>Log in to Epic</strong> below.</li>
              <li>Open <a class="lj-link" href="https://legendary.gl/epiclogin" target="_blank" rel="noopener noreferrer">legendary.gl/epiclogin</a>
                  and sign in to Epic. The page then shows a short JSON text.</li>
              <li>Copy the value of <code>authorizationCode</code> into the answer box.</li>
            </ol>
            <lj-login-relay service="epic" label="Epic" startLabel="Log in to Epic" (finished)="reload()" />
          } @else {
            <p><cds-icon shape="check-circle" status="success" aria-hidden="true"></cds-icon> Logged in to Epic.
              <button type="button" class="btn btn-sm btn-link" (click)="logout('epic')">Log out</button></p>
          }

          <form class="clr-form clr-form-vertical" (ngSubmit)="save()">
            <div class="clr-toggle-wrapper">
              <input type="checkbox" role="switch" id="epic-on" class="clr-toggle" [(ngModel)]="settings.epic.enabled" name="epicOn">
              <label for="epic-on">Prefill Epic games</label>
            </div>
            <div class="clr-checkbox-wrapper">
              <input type="checkbox" id="epic-all" class="clr-checkbox" [(ngModel)]="settings.epic.all" name="epicAll">
              <label for="epic-all">My whole library</label>
            </div>

            @if (!settings.epic.all) {
              <fieldset class="lj-fieldset">
                <legend>Games from your library</legend>
                <p class="lj-muted">
                  @if (epic()?.updated) { Library read {{ when(epic()!.updated!) }} — {{ epic()!.games.length }} games. }
                  @else { Your library hasn't been read yet. }
                  <button type="button" class="btn btn-sm btn-link" (click)="refreshEpic()"
                          [disabled]="!d.loggedIn.epic || epicRefreshing()">
                    {{ epicRefreshing() ? 'Reading library…' : 'Refresh library' }}
                  </button>
                </p>
                @if (epic()?.refresh?.state === 'failed') { <p class="lj-error">{{ epic()!.refresh.message }}</p> }
                @if (epic()?.games?.length) {
                  <label for="epic-filter">Filter</label>
                  <input type="search" id="epic-filter" class="clr-input lj-wide" name="epicFilter" [(ngModel)]="epicFilter"
                         (ngModelChange)="epicFilterSig.set($event)" autocomplete="off">
                  <div class="lj-checkgrid lj-scrollbox">
                    @for (g of filteredEpic(); track g.id) {
                      <div class="clr-checkbox-wrapper">
                        <input type="checkbox" class="clr-checkbox" [id]="'ep-' + g.id" [name]="'ep-' + g.id"
                               [checked]="g.id in settings.epic.games" (change)="toggleEpic(g, $any($event.target).checked)">
                        <label [for]="'ep-' + g.id">{{ g.title }}</label>
                      </div>
                    }
                  </div>
                  <p class="lj-muted">{{ epicSelectedCount() }} selected.</p>
                }
              </fieldset>
            }
            <button type="submit" class="btn btn-primary" [disabled]="busy()">Save Epic settings</button>
          </form>
        </div>
      </section>

      <!-- Battle.net -->
      <section class="card lj-card-section" aria-labelledby="bnet-h">
        <div class="card-block">
          <h2 id="bnet-h" class="card-title">Battle.net</h2>
          <p>Blizzard, Activision and Microsoft games on Battle.net — no login needed.</p>
          <form class="clr-form clr-form-vertical" (ngSubmit)="save()">
            <div class="clr-toggle-wrapper">
              <input type="checkbox" role="switch" id="bnet-on" class="clr-toggle" [(ngModel)]="settings.battlenet.enabled" name="bnetOn">
              <label for="bnet-on">Prefill Battle.net games</label>
            </div>
            @for (pub of publishers(); track pub) {
              <fieldset class="lj-fieldset">
                <legend>{{ pub }}</legend>
                <div class="lj-checkgrid">
                  @for (p of productsBy(pub); track p.code) {
                    <div class="clr-checkbox-wrapper">
                      <input type="checkbox" class="clr-checkbox" [id]="'bn-' + p.code" [name]="'bn-' + p.code"
                             [checked]="settings.battlenet.products.includes(p.code)"
                             (change)="toggleProduct(p.code, $any($event.target).checked)">
                      <label [for]="'bn-' + p.code">{{ p.name }}</label>
                    </div>
                  }
                </div>
              </fieldset>
            }
            <button type="submit" class="btn btn-primary" [disabled]="busy()">Save Battle.net settings</button>
          </form>
        </div>
      </section>

      <!-- Last runs -->
      <section class="lj-section" aria-labelledby="logs-h">
        <h2 id="logs-h">Last runs</h2>
        @for (svc of services; track svc) {
          @let r = d.status.services[svc];
          <h3 class="lj-subhead">{{ label(svc) }}
            @if (r.lastRun) {
              @if (r.lastRun.running) { <span class="label label-info">running</span> }
              @else if (r.lastRun.exitCode === 0) { <span class="label label-success">finished</span> }
              @else { <span class="label label-danger">failed ({{ r.lastRun.exitCode }})</span> }
              <small class="lj-muted">{{ when(r.lastRun.finishedAt ?? r.lastRun.startedAt) }}</small>
            } @else { <small class="lj-muted">never run</small> }
          </h3>
          @if (r.error) { <p class="lj-error">{{ r.error }}</p> }
          @if (r.log) { <pre class="lj-pre" tabindex="0">{{ r.log }}</pre> }
        }
      </section>
    } @else {
      <span class="spinner spinner-inline">Loading…</span> Loading…
    }
  `,
})
export class PrefillPage {
  private readonly api = inject(Api);
  protected readonly data = signal<Prefill | null>(null);
  protected readonly epic = signal<EpicLibrary | null>(null);
  protected readonly epicFilterSig = signal('');
  protected readonly epicSelectedCount = signal(0);
  protected readonly results = signal<SteamGame[]>([]);
  protected readonly busy = signal(false);
  protected readonly message = signal('');
  protected readonly gameIds = signal<string[]>([]);
  protected readonly services = ['steam', 'epic', 'battlenet'] as const;
  protected settings: PrefillSettings = {
    steam: { enabled: false, all: false, recent: false, top: 0, os: ['windows'], games: {} },
    epic: { enabled: false, all: false, games: {} },
    battlenet: { enabled: false, products: [] },
    schedule: { enabled: false, time: '03:00' },
  };
  protected query = '';
  protected epicFilter = '';
  private loadedSettings = false;
  private searchTimer?: ReturnType<typeof setTimeout>;

  protected readonly epicRefreshing = computed(
    () => !!this.data()?.status.refreshingEpic || this.epic()?.refresh.state === 'running',
  );
  protected readonly filteredEpic = computed(() => {
    const f = this.epicFilterSig().trim().toLowerCase();
    const games = this.epic()?.games ?? [];
    return f ? games.filter((g) => g.title.toLowerCase().includes(f)) : games;
  });
  protected readonly publishers = computed(() => [...new Set((this.data()?.battlenetProducts ?? []).map((p) => p.publisher))]);

  constructor() {
    poll(() => this.load(), 5000);
  }

  protected reload() {
    return this.load();
  }

  private async load() {
    try {
      const d = await this.api.prefill();
      this.data.set(d);
      if (!this.loadedSettings) {
        this.settings = structuredClone(d.settings);
        this.gameIds.set(Object.keys(this.settings.steam.games));
        this.epicSelectedCount.set(Object.keys(this.settings.epic.games).length);
        this.loadedSettings = true;
      }
      this.epic.set(await this.api.epicLibrary());
    } catch (e) {
      this.message.set(errorMessage(e));
    }
  }

  protected label(s: string) {
    return ({ steam: 'Steam', epic: 'Epic Games', battlenet: 'Battle.net' } as Record<string, string>)[s] ?? s;
  }

  protected queuedLabels() {
    return (this.data()?.status.queued ?? []).map((s) => this.label(s)).join(', ');
  }

  protected when(iso: string) {
    return new Date(iso).toLocaleString();
  }

  protected productsBy(pub: string) {
    return (this.data()?.battlenetProducts ?? []).filter((p) => p.publisher === pub);
  }

  protected selected(id: string) {
    return id in this.settings.steam.games;
  }

  protected search(q: string) {
    clearTimeout(this.searchTimer);
    if (q.trim().length < 2) {
      this.results.set([]);
      return;
    }
    this.searchTimer = setTimeout(async () => {
      try {
        this.results.set((await this.api.steamSearch(q)).results);
      } catch (e) {
        this.message.set(errorMessage(e));
      }
    }, 300);
  }

  protected add(g: SteamGame) {
    this.settings.steam.games[g.id] = g.name;
    this.gameIds.set(Object.keys(this.settings.steam.games));
    this.message.set(`${g.name} added — save to keep it.`);
  }

  protected remove(id: string) {
    const name = this.settings.steam.games[id];
    delete this.settings.steam.games[id];
    this.gameIds.set(Object.keys(this.settings.steam.games));
    this.message.set(`${name} removed — save to keep the change.`);
  }

  protected toggleOs(os: string, on: boolean) {
    const set = new Set(this.settings.steam.os);
    on ? set.add(os) : set.delete(os);
    this.settings.steam.os = [...set];
  }

  protected toggleProduct(code: string, on: boolean) {
    const set = new Set(this.settings.battlenet.products);
    on ? set.add(code) : set.delete(code);
    this.settings.battlenet.products = [...set];
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

  protected save() {
    return this.act(async () => {
      this.settings = (await this.api.savePrefill(this.settings)).settings;
      this.gameIds.set(Object.keys(this.settings.steam.games));
    }, 'Saved.');
  }

  protected run() {
    return this.act(() => this.api.runPrefill(), 'Prefill started.');
  }

  protected stop() {
    return this.act(() => this.api.stopPrefill(), 'Prefill stopped — it continues where it left off next time.');
  }

  protected toggleEpic(g: { id: string; title: string }, on: boolean) {
    if (on) this.settings.epic.games[g.id] = g.title;
    else delete this.settings.epic.games[g.id];
    this.epicSelectedCount.set(Object.keys(this.settings.epic.games).length);
  }

  protected refreshEpic() {
    return this.act(() => this.api.refreshEpicLibrary(), 'Reading your Epic library…');
  }

  protected logout(service: LoginService) {
    return this.act(() => this.api.serviceLogout(service), `Logged out of ${this.label(service)}.`);
  }
}
