import { Component, ElementRef, inject, input, output, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, LoginService, LoginStatus, errorMessage } from './api';
import { poll } from './poll';

/**
 * Relays a prefill tool's interactive login (running on the server) into the
 * page: shows its output and sends answers back. Inputs that look like a
 * password or auth code are masked, and the server scrubs them from the
 * transcript.
 */
@Component({
  selector: 'lj-login-relay',
  imports: [FormsModule],
  template: `
    @if (!status() || status()!.state !== 'running') {
      <button type="button" class="btn btn-primary" (click)="start()" [disabled]="busy()">{{ startLabel() }}</button>
    }
    @if (status(); as l) {
      @if (l.state !== 'idle') {
        <div class="lj-terminal">
          <pre class="lj-pre" role="log" [attr.aria-label]="label() + ' login output'" tabindex="0" #term>{{ l.output || 'Starting…' }}</pre>
          @if (l.state === 'running') {
            <ng-content />
            <form class="lj-term-input" (ngSubmit)="send()">
              <label [for]="service() + '-term-input'">{{ l.prompt || 'Answer' }}</label>
              <input [id]="service() + '-term-input'" name="termInput" class="clr-input" [type]="l.secret ? 'password' : 'text'"
                     [(ngModel)]="answer" autocomplete="off" spellcheck="false" autocapitalize="off" enterkeyhint="send">
              <button type="submit" class="btn btn-sm btn-primary">Send</button>
            </form>
          } @else {
            <p role="status">{{ l.loggedIn ? 'Login complete.' : 'Login ended without success — try again.' }}</p>
          }
        </div>
      }
    }
    <p class="lj-error" role="alert">{{ error() }}</p>
  `,
})
export class LoginRelay {
  readonly service = input.required<LoginService>();
  readonly label = input.required<string>();
  readonly startLabel = input('Log in');
  readonly finished = output<boolean>();

  private readonly api = inject(Api);
  protected readonly status = signal<LoginStatus | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal('');
  protected answer = '';
  private readonly term = viewChild<ElementRef<HTMLPreElement>>('term');

  constructor() {
    poll(() => this.refresh(), 1500);
  }

  private async refresh() {
    const prev = this.status();
    if (prev && prev.state !== 'running') return; // only poll while a login is active
    try {
      const s = await this.api.loginStatus(this.service());
      this.status.set(s);
      queueMicrotask(() => {
        const el = this.term()?.nativeElement;
        if (el) el.scrollTop = el.scrollHeight; // keep the newest output in view
      });
      if (prev?.state === 'running' && s.state === 'finished') this.finished.emit(s.loggedIn);
    } catch {
      /* page shows its own errors */
    }
  }

  protected async start() {
    this.busy.set(true);
    this.error.set('');
    try {
      this.status.set(await this.api.startLogin(this.service()));
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async send() {
    const text = this.answer;
    this.answer = '';
    try {
      await this.api.loginInput(this.service(), text, !!this.status()?.secret);
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }
}
