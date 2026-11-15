import { Component, input } from '@angular/core';

import { Job } from './api';

/** Output of the current/last compose job. */
@Component({
  selector: 'lj-job-panel',
  template: `
    @if (job(); as j) {
      <div class="lj-job">
        <p role="status">
          @switch (j.state) {
            @case ('running') { <span class="spinner spinner-inline">Running</span> Running {{ j.action }}… }
            @case ('succeeded') { <span class="label label-success">Done</span> {{ j.action }} finished. }
            @case ('failed') { <span class="label label-danger">Failed</span> {{ j.action }} failed — see the output below. }
          }
        </p>
        <pre class="lj-pre" tabindex="0">{{ j.output }}</pre>
      </div>
    }
  `,
})
export class JobPanel {
  readonly job = input<Job | null>(null);
}
