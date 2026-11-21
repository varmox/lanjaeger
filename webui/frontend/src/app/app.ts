import { Component, computed, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ClrIcon } from '@clr/angular/icon';
import { ClrMainContainerModule } from '@clr/angular/layout/main-container';
import { ClrNavigationModule } from '@clr/angular/layout/nav';
import { ClrVerticalNavModule } from '@clr/angular/layout/vertical-nav';

import { Api } from './shared/api';
import { ThemeService } from './shared/theme';

@Component({
  selector: 'lj-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ClrMainContainerModule, ClrNavigationModule, ClrVerticalNavModule, ClrIcon],
  template: `
    <clr-main-container>
      <clr-header class="header-6">
        <div class="branding">
          <a routerLink="/dashboard" class="nav-link">
            <cds-icon shape="host" size="lg" aria-hidden="true"></cds-icon>
            <span class="title">lanjaeger</span>
          </a>
        </div>
        <div class="header-actions">
          <button type="button" class="nav-link nav-icon lj-header-btn" (click)="theme.toggle()"
                  [attr.aria-label]="theme.theme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'">
            <cds-icon [shape]="theme.theme() === 'dark' ? 'sun' : 'moon'" aria-hidden="true"></cds-icon>
          </button>
          @if (session()?.signedIn) {
            <button type="button" class="nav-link nav-icon lj-header-btn" (click)="logout()" aria-label="Sign out">
              <cds-icon shape="logout" aria-hidden="true"></cds-icon>
            </button>
          }
        </div>
      </clr-header>

      <div class="content-container">
        @if (showNav()) {
          <!-- clr-nav-level=1: off-canvas behind a hamburger on small screens -->
          <clr-vertical-nav [clrVerticalNavCollapsible]="true" [clr-nav-level]="1">
            <a clrVerticalNavLink routerLink="/dashboard" routerLinkActive="active">
              <cds-icon clrVerticalNavIcon shape="dashboard" aria-hidden="true"></cds-icon>Dashboard
            </a>
            <a clrVerticalNavLink routerLink="/clients" routerLinkActive="active">
              <cds-icon clrVerticalNavIcon shape="computer" aria-hidden="true"></cds-icon>Clients
            </a>
            <a clrVerticalNavLink routerLink="/adblock" routerLinkActive="active">
              <cds-icon clrVerticalNavIcon shape="shield" aria-hidden="true"></cds-icon>Ad blocking
            </a>
            @if (session()?.signedIn) {
              <a clrVerticalNavLink routerLink="/prefill" routerLinkActive="active">
                <cds-icon clrVerticalNavIcon shape="download" aria-hidden="true"></cds-icon>Prefill
              </a>
              <a clrVerticalNavLink routerLink="/dns" routerLinkActive="active">
                <cds-icon clrVerticalNavIcon shape="network-globe" aria-hidden="true"></cds-icon>DNS entries
              </a>
              <a clrVerticalNavLink routerLink="/alerts" routerLinkActive="active">
                <cds-icon clrVerticalNavIcon shape="bell" aria-hidden="true"></cds-icon>Alerts
              </a>
              <a clrVerticalNavLink routerLink="/services" routerLinkActive="active">
                <cds-icon clrVerticalNavIcon shape="terminal" aria-hidden="true"></cds-icon>Services
              </a>
              <a clrVerticalNavLink routerLink="/settings" routerLinkActive="active">
                <cds-icon clrVerticalNavIcon shape="cog" aria-hidden="true"></cds-icon>Settings
              </a>
            } @else {
              <a clrVerticalNavLink routerLink="/login" routerLinkActive="active">
                <cds-icon clrVerticalNavIcon shape="logout" aria-hidden="true"></cds-icon>Sign in
              </a>
            }
          </clr-vertical-nav>
        }
        <main class="content-area" id="main">
          <router-outlet />
        </main>
      </div>
    </clr-main-container>
  `,
})
export class App {
  protected readonly theme = inject(ThemeService);
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  protected readonly session = this.api.session;
  protected readonly showNav = computed(() => {
    const s = this.session();
    return !!s && (s.signedIn || s.publicDashboard);
  });

  constructor() {
    void this.api.refreshSession();
  }

  protected async logout() {
    await this.api.logout();
    await this.router.navigateByUrl('/login');
  }
}
