import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';

import { Api } from './shared/api';

/** Stats pages: signed in, or the dashboard is public. */
const canRead: CanActivateFn = async () => {
  const api = inject(Api);
  const s = api.session() ?? (await api.refreshSession());
  return s.signedIn || s.publicDashboard || inject(Router).parseUrl('/login');
};

/** Controls: signed in only. */
const canAdmin: CanActivateFn = async () => {
  const api = inject(Api);
  const s = api.session() ?? (await api.refreshSession());
  return s.signedIn || inject(Router).parseUrl('/login');
};

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  // Public on purpose: players open it to test their own device.
  { path: 'check', title: 'Is my device using the cache? · lanjaeger', loadComponent: () => import('./pages/check').then((m) => m.CheckPage) },
  { path: 'login', title: 'Sign in · lanjaeger', loadComponent: () => import('./pages/login').then((m) => m.LoginPage) },
  { path: 'dashboard', title: 'Dashboard · lanjaeger', canActivate: [canRead], loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage) },
  { path: 'clients', title: 'Clients · lanjaeger', canActivate: [canRead], loadComponent: () => import('./pages/clients').then((m) => m.ClientsPage) },
  { path: 'adblock', title: 'Ad blocking · lanjaeger', canActivate: [canRead], loadComponent: () => import('./pages/adblock').then((m) => m.AdblockPage) },
  { path: 'prefill', title: 'Prefill · lanjaeger', canActivate: [canAdmin], loadComponent: () => import('./pages/prefill').then((m) => m.PrefillPage) },
  { path: 'dns', title: 'DNS entries · lanjaeger', canActivate: [canAdmin], loadComponent: () => import('./pages/dns').then((m) => m.DnsPage) },
  { path: 'alerts', title: 'Alerts · lanjaeger', canActivate: [canAdmin], loadComponent: () => import('./pages/alerts').then((m) => m.AlertsPage) },
  { path: 'services', title: 'Services · lanjaeger', canActivate: [canAdmin], loadComponent: () => import('./pages/services').then((m) => m.ServicesPage) },
  { path: 'settings', title: 'Settings · lanjaeger', canActivate: [canAdmin], loadComponent: () => import('./pages/settings').then((m) => m.SettingsPage) },
  { path: '**', redirectTo: 'dashboard' },
];
