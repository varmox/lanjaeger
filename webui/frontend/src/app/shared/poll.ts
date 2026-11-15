import { DestroyRef, inject } from '@angular/core';

/**
 * Runs `fn` now and every `ms` while the tab is visible; pauses in background
 * tabs (no wasted requests) and refreshes immediately when the tab returns.
 * Stops when the calling component is destroyed.
 */
export function poll(fn: () => Promise<unknown>, ms: number): void {
  const destroyRef = inject(DestroyRef);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;

  const tick = async () => {
    clearTimeout(timer);
    if (stopped || document.visibilityState !== 'visible') return;
    try {
      await fn();
    } finally {
      if (!stopped) timer = setTimeout(tick, ms);
    }
  };
  const onVisibility = () => {
    if (document.visibilityState === 'visible') void tick();
    else clearTimeout(timer);
  };

  document.addEventListener('visibilitychange', onVisibility);
  destroyRef.onDestroy(() => {
    stopped = true;
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', onVisibility);
  });
  void tick();
}
