import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

/** Every API call carries this header; the server refuses state changes without it (CSRF). */
export const csrfHeader: HttpInterceptorFn = (req, next) =>
  next(req.url.startsWith('/api/') ? req.clone({ setHeaders: { 'X-Lanjaeger': '1' } }) : req);

export interface Session {
  signedIn: boolean;
  passwordSet: boolean;
  publicDashboard: boolean;
  controls: boolean;
  project: string | null;
}

export interface Counter {
  hitBytes: number;
  missBytes: number;
  requests: number;
  lastSeen: number;
}

export interface Stats {
  since: number | null;
  total: Counter;
  services: (Counter & { name: string })[];
  clients: (Counter & { ip: string })[];
  series: { t: number; hit: number; miss: number }[];
  bucketSeconds: number;
  active: { client: string; service: string; bytesPerSecond: number }[];
}

export interface Disk {
  filesystemTotal: number | null;
  filesystemFree: number | null;
  cacheBytes: number | null;
  cacheScannedAt: number | null;
  cacheLimit: number | null;
}

export interface Service {
  id: string;
  service: string;
  image: string;
  state: string;
  status: string;
  health: 'healthy' | 'unhealthy' | 'starting' | null;
}

export interface ConfigField {
  key: string;
  value: string;
  group: 'network' | 'storage' | 'logs' | 'operation';
  help: string;
  options?: string[];
}

export interface Job {
  action: 'update' | 'apply';
  state: 'running' | 'succeeded' | 'failed';
  output: string;
  started: number;
  finished: number | null;
}

export interface NameCount {
  name: string;
  count: number;
}

export interface Adblock {
  available: boolean;
  error?: string;
  active?: boolean;
  pausedForSeconds?: number;
  persistedEnabled: boolean;
  summary?: { queries: number; blocked: number; cached: number; forwarded: number; errors: number; avgResponseMs: number; cacheHitRate: number };
  perHour?: { hour: string; queries: number; blocked: number; filtered: number }[];
  topBlockedDomains?: NameCount[];
  topClients?: NameCount[];
  lists?: { denylist?: Record<string, number>; allowlist?: Record<string, number> };
}

export interface PrefillSettings {
  steam: { enabled: boolean; all: boolean; recent: boolean; top: number; os: string[]; games: Record<string, string> };
  epic: { enabled: boolean; all: boolean; games: Record<string, string> };
  battlenet: { enabled: boolean; products: string[] };
  schedule: { enabled: boolean; time: string };
}

export interface PrefillRun {
  lastRun: { running: boolean; exitCode: number; startedAt: string; finishedAt: string | null } | null;
  log: string;
  error: string | null;
}

export type PrefillService = 'steam' | 'epic' | 'battlenet';
export type LoginService = 'steam' | 'epic';

export interface Prefill {
  settings: PrefillSettings;
  loggedIn: Record<PrefillService, boolean>;
  runnable: string[];
  battlenetProducts: { code: string; name: string; publisher: string }[];
  status: { running: string | null; refreshingEpic: boolean; queued: string[]; services: Record<PrefillService, PrefillRun> };
}

export interface EpicLibrary {
  updated: string | null;
  games: { id: string; title: string }[];
  refresh: { state: 'idle' | 'running' | 'done' | 'failed'; message: string };
}

export interface LoginStatus {
  state: 'idle' | 'running' | 'finished';
  exitCode: number | null;
  output: string;
  prompt: string;
  secret: boolean;
  loggedIn: boolean;
}

export interface SteamGame {
  id: string;
  name: string;
  image: string;
}

export interface AlertSettings {
  enabled: boolean;
  repeatHours: number;
  rules: { diskPercent: number; diskFreeGb: number; serviceDown: boolean; serviceDownMinutes: number;
           prefillFailed: boolean; adblockEmpty: boolean };
  channels: {
    ntfy: { enabled: boolean; url: string; token: string };
    discord: { enabled: boolean; webhook: string };
    email: { enabled: boolean; host: string; port: number; security: 'starttls' | 'ssl' | 'none';
             username: string; password: string; from: string; to: string };
  };
}

export interface AlertEntry {
  key: string;
  severity: 'critical' | 'warning' | 'resolved' | 'info';
  title: string;
  body?: string;
  firedAt?: number;
}

export interface Alerts {
  settings: AlertSettings;
  active: AlertEntry[];
  history: { time: number; key: string; kind: 'fired' | 'repeat' | 'resolved'; severity: string; title: string;
             results: Record<string, string> }[];
}

/** Placeholder the server sends instead of a stored secret; send it back to keep it. */
export const SECRET_PLACEHOLDER = '__unchanged__';

/** Message from an API error body, or a generic one. */
export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    return err.error?.error ?? (err.status === 0 ? 'The server is not reachable.' : err.message);
  }
  return String(err);
}

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  /** Current session; null until the first check has finished. */
  readonly session = signal<Session | null>(null);

  async refreshSession(): Promise<Session> {
    const s = await firstValueFrom(this.http.get<Session>('/api/session'));
    this.session.set(s);
    return s;
  }

  async login(password: string): Promise<void> {
    await firstValueFrom(this.http.post('/api/login', { password }));
    await this.refreshSession();
  }

  async logout(): Promise<void> {
    await firstValueFrom(this.http.post('/api/logout', {}));
    await this.refreshSession();
  }

  stats() {
    return firstValueFrom(this.http.get<Stats>('/api/stats'));
  }

  disk() {
    return firstValueFrom(this.http.get<Disk>('/api/disk'));
  }

  services() {
    return firstValueFrom(
      this.http.get<{ available: boolean; services: Service[]; managed?: string[] }>('/api/services'),
    );
  }

  restart(service: string) {
    return firstValueFrom(this.http.post(`/api/services/${encodeURIComponent(service)}/restart`, {}));
  }

  logs(service: string) {
    return firstValueFrom(
      this.http.get<{ text: string }>(`/api/services/${encodeURIComponent(service)}/logs`),
    );
  }

  config() {
    return firstValueFrom(this.http.get<{ fields: ConfigField[] }>('/api/config'));
  }

  saveConfig(values: Record<string, string>) {
    return firstValueFrom(this.http.put<{ changed: string[] }>('/api/config', { values }));
  }

  startJob(action: Job['action']) {
    return firstValueFrom(this.http.post<Job>('/api/jobs', { action }));
  }

  adblock() {
    return firstValueFrom(this.http.get<Adblock>('/api/adblock'));
  }

  setBlocking(enabled: boolean, minutes?: number) {
    return firstValueFrom(this.http.post('/api/adblock/blocking', minutes ? { enabled, minutes } : { enabled }));
  }

  adblockLists() {
    return firstValueFrom(this.http.get<{ allow: string[]; deny: string[] }>('/api/adblock/lists'));
  }

  saveAdblockList(kind: 'allow' | 'deny', domains: string[]) {
    return firstValueFrom(
      this.http.put<{ domains: string[]; refreshed: boolean }>(`/api/adblock/lists/${kind}`, { domains }),
    );
  }

  prefill() {
    return firstValueFrom(this.http.get<Prefill>('/api/prefill'));
  }

  savePrefill(settings: PrefillSettings) {
    return firstValueFrom(this.http.put<{ settings: PrefillSettings }>('/api/prefill/settings', { settings }));
  }

  runPrefill(services?: string[]) {
    return firstValueFrom(this.http.post<{ started: string[] }>('/api/prefill/run', services ? { services } : {}));
  }

  stopPrefill() {
    return firstValueFrom(this.http.post('/api/prefill/stop', {}));
  }

  steamSearch(q: string) {
    return firstValueFrom(
      this.http.get<{ results: SteamGame[] }>('/api/prefill/steam/search', { params: { q } }),
    );
  }

  loginStatus(service: LoginService) {
    return firstValueFrom(this.http.get<LoginStatus>(`/api/prefill/${service}/login`));
  }

  startLogin(service: LoginService) {
    return firstValueFrom(this.http.post<LoginStatus>(`/api/prefill/${service}/login`, {}));
  }

  loginInput(service: LoginService, text: string, secret: boolean) {
    return firstValueFrom(this.http.post(`/api/prefill/${service}/login/input`, { text, secret }));
  }

  logout(service: LoginService) {
    return firstValueFrom(this.http.post(`/api/prefill/${service}/logout`, {}));
  }

  epicLibrary() {
    return firstValueFrom(this.http.get<EpicLibrary>('/api/prefill/epic/library'));
  }

  refreshEpicLibrary() {
    return firstValueFrom(this.http.post('/api/prefill/epic/library/refresh', {}));
  }

  alerts() {
    return firstValueFrom(this.http.get<Alerts>('/api/alerts'));
  }

  activeAlerts() {
    return firstValueFrom(this.http.get<{ active: { title: string; severity: string }[] }>('/api/alerts/active'));
  }

  saveAlerts(settings: AlertSettings) {
    return firstValueFrom(this.http.put<{ settings: AlertSettings }>('/api/alerts/settings', { settings }));
  }

  testAlerts() {
    return firstValueFrom(this.http.post<{ results: Record<string, string> }>('/api/alerts/test', {}));
  }

  job() {
    return firstValueFrom(this.http.get<{ job: Job | null }>('/api/jobs/current'));
  }
}
