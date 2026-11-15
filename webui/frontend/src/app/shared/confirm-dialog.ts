import { Component, ElementRef, input, viewChild } from '@angular/core';

/**
 * Confirmation built on the native <dialog> (modal via showModal()): the
 * browser provides focus containment, Esc, inert background and focus
 * return — so no role/aria-modal/focus-trap code here. Styled with Clarity's
 * modal classes.
 *
 *   const ok = await this.confirm().ask();
 */
@Component({
  selector: 'lj-confirm-dialog',
  template: `
    <dialog #dlg class="lj-dialog" [attr.aria-labelledby]="id + '-title'" closedby="any">
      <form method="dialog" class="modal-content">
        <div class="modal-header--accessible">
          <h3 class="modal-title" [id]="id + '-title'">{{ heading() }}</h3>
        </div>
        <div class="modal-body"><ng-content /></div>
        <div class="modal-footer">
          <!-- Safe action gets focus first in a destructive confirmation. -->
          <button class="btn btn-outline" value="cancel" autofocus>Cancel</button>
          <button class="btn" [class.btn-danger]="danger()" [class.btn-primary]="!danger()" value="ok">
            {{ confirmLabel() }}
          </button>
        </div>
      </form>
    </dialog>
  `,
})
export class ConfirmDialog {
  readonly heading = input.required<string>();
  readonly confirmLabel = input('Confirm');
  readonly danger = input(false);

  private static seq = 0;
  protected readonly id = `lj-confirm-${ConfirmDialog.seq++}`;
  private readonly dlg = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');

  ask(): Promise<boolean> {
    const d = this.dlg().nativeElement;
    d.returnValue = '';
    d.showModal();
    return new Promise((resolve) => {
      d.addEventListener('close', () => resolve(d.returnValue === 'ok'), { once: true });
    });
  }
}
