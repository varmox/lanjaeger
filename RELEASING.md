# Releases and maintenance

Everything runs on GitHub Actions. For a public repository, the standard runners, Dependabot, CodeQL, GitHub Releases and the 14-day OVA downloads are all free.

## What runs when

| Workflow | When | What it does |
|---|---|---|
| **CI** | every push and pull request; **Sundays**; on demand | shellcheck, compose configs, Blocky config, backend tests, frontend build, web UI image, the real stack (DNS, check page, DNS entries, metrics, Telegraf → Prometheus → Grafana). A failing Sunday run opens an issue labelled `maintenance`. |
| **Appliance** | pushes that touch the stack or `appliance/`; tags `v*`; **1st of each month**; on demand | Builds the Photon OS OVA, checks the OVF, boots it in QEMU with deployment options and tests the stack. Every build is kept as a downloadable artifact for 14 days. A tag attaches the OVA to the release. The monthly build replaces the `appliance-latest` pre-release, so Photon OS security fixes reach new deployments. |
| **Maintenance** | **Mondays**; on demand | Looks for newer stable versions of the pinned images (Blocky, Telegraf, Prometheus, Grafana, busybox, prefill tools) that are at least 7 days old. Updates them in one pull request (`maintenance/image-updates`) and runs CI on it. |
| **Dependabot** | weekly | Pull requests for GitHub Actions, the frontend's npm packages (Angular/Clarity grouped) and the web UI's base images. Security updates whenever an advisory appears. |
| **CodeQL** | pushes to main, pull requests, Wednesdays | Security scanning of the Python backend, the TypeScript frontend and the workflows. Results: *Security → Code scanning*. |
| **Release** | on demand | See below. |

lancache's own images (`monolithic`, `lancache-dns`) are on `:latest`, the way upstream publishes them. They're covered by the Sunday CI run, which tests the latest versions, and by the monthly appliance build, which ships them.

## Making a release

1. Make sure CI on `main` is green.
2. *Actions → Release → Run workflow* and enter the version, e.g. `1.2.0`. Tick *pre-release* for a test release.
3. The workflow:
   - checks that CI passed on that commit;
   - tags it `v1.2.0` and creates the release with notes generated from the merged pull requests (grouped by the labels `appliance` and `dependencies`);
   - starts the appliance build, which attaches `lanjaeger-1.2.0.ova` and its `.sha256` after about 30 minutes.

Pushing a tag yourself (`git tag v1.2.0 && git push origin v1.2.0`) also builds and attaches the OVA. Create the release notes in the GitHub UI in that case.

**Versions:** `MAJOR.MINOR.PATCH`. Bump MAJOR when users must change something (`.env` keys, ports, deployment options). MINOR for new features. PATCH for fixes and image updates.

## Weekly routine (5 minutes)

- Merge the **Maintenance** and **Dependabot** pull requests once CI is green. Grafana or Prometheus major versions deserve a look at their release notes, and at the dashboard: download the `monitoring` artifact and check `dashboard.png`.
- Close the `maintenance` issue once a later Sunday run passes.
- After merging image updates, cut a PATCH release if appliance users should get them. Existing installations update with *web UI → Services → Update*.

## One-time repository settings

These need a repository admin. Without the first one, the Maintenance workflow can't open its pull request.

- *Settings → Actions → General → Workflow permissions:* leave "Read repository contents" selected, and tick **Allow GitHub Actions to create and approve pull requests**.
- *Settings → Advanced Security* (or *Code security*): enable **Dependabot alerts** and **Dependabot security updates**.
- Optional: *Settings → General → Automatically delete head branches*.
- Optional: a branch protection rule (ruleset) for `main` that requires the CI checks `stack`, `webui` and `integration`.
