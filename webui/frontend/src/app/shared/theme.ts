import { Injectable, signal } from '@angular/core';

export type Theme = 'light' | 'dark';

/**
 * Clarity themes via the `cds-theme` attribute on <body>; native UI follows
 * through `color-scheme` on the root (set before paint by theme-init.js).
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly theme = signal<Theme>(
    (document.documentElement.getAttribute('data-theme') as Theme) ?? 'light',
  );

  toggle(): void {
    this.apply(this.theme() === 'dark' ? 'light' : 'dark');
  }

  private apply(theme: Theme): void {
    this.theme.set(theme);
    document.body.setAttribute('cds-theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem('lj-theme', theme);
    } catch {
      // private mode: the choice just isn't remembered
    }
  }
}
