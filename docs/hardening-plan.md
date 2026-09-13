# Windows deployment hardening

## Workflow

- [x] Read the Principles section of Poteto Mode in full.
- [x] Phase A: Frame.
- [x] Phase B: Design the workflow.
- [x] Phase C: Run the loop.
- [x] Audit Windows installation, launchers, updates, and port conflicts.
- [x] Audit WSJT-X Improved protocol parsing, reconnects, malformed input, and duplicate handling.
- [x] Audit database transactions, backups, restart recovery, and filesystem failures.
- [x] Audit configuration, service lifecycle, API, and dashboard failure reporting.
- [x] Add reproducible regression checks and Windows CI; verify an installed distribution.
- [x] Phase D: Keep the audit trail.
- [x] Phase E: Verify and hand back.

## Acceptance

The existing suite and new failure regressions must pass. A fresh installed wheel must receive synthetic UDP, persist observations, serve the dashboard, export CSV, and produce a verified backup using temporary data. Windows automation must exercise supported Python runtimes. Tomorrow's deployment needs a real WSJT-X Improved decode, backup verification, and restart check on the target machine.

## Scope and throughput checkpoint

Four bounded workstreams cover installation, protocol, storage, and application lifecycle. Each gets an independent audit before fixes. Workers own disjoint files. The coordinator reviews the combined diff and reproduces results. Existing normalized decodes, runtime state, and SQLAlchemy models remain the data shapes unless evidence requires a narrower change. Boundary validation and idempotent lifecycle handling are preferred over a rewrite.

No Windows host or receiver is attached to this session. Hardware, Windows policy, audio routing, and actual RF reception remain target-machine acceptance checks. This run must distinguish tested behavior from those open checks.

## Parallel review

- Frame: four disjoint audit slices, each reporting evidenced failures and verification.
- Fan out: Windows launchers; protocol and ingest boundary; database and backup.
- Aggregate: review each result and the integrated behavior.
- Report: retain unresolved machine-specific checks in the deployment guide.

## Design gate

Ground the runtime flow before editing. Sketch alternative shapes for any lifecycle or ownership change and choose the smallest that addresses the reproduced failure. Agree proceeds autonomously. Implement against that choice. Scrap only if repeated evidence disproves the selected shape. Routine validation and local defect repairs do not require a new architecture.

## Decisions and evidence

Boundary Discipline put input checks in YAML and WSJT-X parsers. Model the Domain kept the existing typed messages and added one bounded queue of packet bytes, source address, and receipt times. Make Operations Idempotent put live collection, imports, and setup behind a database lock released by the operating system. Prove It Works and Build the Lever expanded the repeatable installed-package check and regression suite.

The baseline had 116 passing tests. The local integrated suite has 242 passing tests and six Windows-only cases exercised by CI. A fresh wheel passed the installed-package check outside the repository. Browser checks observed live data, a visible warning after malformed UDP, disconnection after server shutdown, and automatic reconnection with old rows preserved. The gstack browser lacked its Chromium runtime, so those UI checks used the built-in browser.

Independent reviews found and corrected the CLI writer lock bypass, unbounded receiver identifiers, and a shutdown deadline that discarded queued packets. Normal shutdown now drains the queue. The review used independent GPT agents because the configured alternative model families were unavailable in this tool set.

The installed deslop and control-cli leaf skills were unavailable. Direct diff review, native Windows CMD tests, installed command execution, and browser checks provide the corresponding verification for this run.

All six [CI jobs](https://github.com/EdmundLimBoEn/japan-hour-logger-v1/actions/runs/34756163407) passed for Windows and Linux on Python 3.12, 3.13, and 3.14. Windows 3.12 also ran the complete Setup Windows.cmd entry point. The remaining live-machine checks are in the setup guide.
