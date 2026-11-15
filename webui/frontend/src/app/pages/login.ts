import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ClrAlertModule } from '@clr/angular/emphasis/alert';
import { ClrPasswordModule } from '@clr/angular/forms/password';

import { Api, errorMessage } from '../shared/api';

@Component({
  selector: 'lj-login',
  imports: [FormsModule, ClrPasswordModule, ClrAlertModule],
  template: `
    <div class="lj-login">
      <h1>Sign in to lanjaeger</h1>

      @if (session()?.passwordSet === false) {
        <clr-alert clrAlertType="warning" [clrAlertClosable]="false">
          <clr-alert-item><span class="alert-text">
            Sign-in is disabled: set <code>WEBUI_PASSWORD</code> in <code>.env</code> and restart the web UI.
          </span></clr-alert-item>
        </clr-alert>
      }

      <!-- A real <form> with stable names so password managers can save and autofill. -->
      <form class="clr-form clr-form-vertical" (ngSubmit)="submit()" method="post" action="/api/login">
        <!-- Single-password app: a fixed username lets password managers file the entry. -->
        <input type="text" name="username" autocomplete="username" value="admin" readonly hidden>

        <clr-password-container>
          <label for="current-password">Password</label>
          <input clrPassword type="password" id="current-password" name="password"
                 autocomplete="current-password" required enterkeyhint="go"
                 [(ngModel)]="password" [disabled]="busy()">
        </clr-password-container>

        <!-- Announced by screen readers when it appears. -->
        <p class="lj-error" role="alert">{{ error() }}</p>

        <button type="submit" class="btn btn-primary btn-block" [disabled]="busy() || session()?.passwordSet === false">
          Sign in
        </button>
      </form>
    </div>
  `,
})
export class LoginPage {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  protected readonly session = this.api.session;
  protected password = '';
  protected readonly busy = signal(false);
  protected readonly error = signal('');

  protected async submit() {
    if (!this.password) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.api.login(this.password);
      await this.router.navigateByUrl('/dashboard');
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
