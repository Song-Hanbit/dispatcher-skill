# Changelog

All notable dispatcher-skill changes are recorded here.

This project uses semantic versioning:

- MAJOR: incompatible runtime, install, data, or operator workflow changes.
- MINOR: backwards-compatible features, UI workflows, helper commands, or documented operating procedures.
- PATCH: backwards-compatible bug fixes, documentation fixes, test updates, and small internal maintenance.

When a coherent todo set changes the package, update `VERSION` and this changelog in the same change. If a todo set is intentionally completed without a release-impacting package change, note that decision in the task result.

## [1.2.1] - 2026-05-22

### Changed

- Removed the non-portable `memory/decisions.md` development decision log from the package payload boundary.
- Clarified that exported packages include selected portable memory docs, not raw development decision logs.

## [1.2.0] - 2026-05-22

### Added

- Added Inbox bulk queue controls for selected or all Inbox tasks, backed by a server API that only moves current Inbox rows and reports skipped items safely.
- Added a Done/Closed card continuation flow that lets users add follow-up instructions and send the same task back to Pending.
- Added `POST /api/tasks/<id>/continue`, `user_continuation` Activity events, and labeled continuation notes in task acceptance criteria.

## [1.1.1] - 2026-05-22

### Changed

- Reworked README around user goals, installation, initialization, the problem Dispatcher Skill solves, and the runtime model.
- Added matching Korean README guidance for installation, operator flow, UI states, and package/state boundaries.

## [1.1.0] - 2026-05-22

### Added

- Added operator and manager initialization memory bootstrap checks.
- Added an initialization compact checkpoint that rejects raw runtime logs, full records, secrets, private keys, and live tunnel URLs in durable memory.
- Manager prompts now include the loaded memory checklist, require compact-equivalent memory cleanup before final task results, and the runner fails a task if a previously clean memory tree becomes contaminated by raw runtime or secret material.

## [1.0.0] - 2026-05-22

### Added

- Established `VERSION` as the dispatcher skill version source of truth.
- Added this changelog and version bump policy.

### Notes

- This release is the baseline for the migrated dispatcher skill payload under `skills/dispatcher-skill/`.
- The prior standalone "version up" todo is handled by establishing this 1.0.0 baseline and the bump policy above.
