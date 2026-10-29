# AtomSculptor diagnostics

Inspect the Run activity and execution details before retrying a failed modelling
action. Completed file writes and external work may already have taken effect.

Model request and tool invocation records use the host's Run StateStore. They
retain redacted application-level inputs, outputs and coverage metadata, not a
raw transport capture. Provider-exposed thinking can be inspected separately;
unexposed or historically unretained content cannot be reconstructed. Interrupted
records are not complete results. Missing token usage remains unknown.

Use `scripts/export-atomsculptor-test.py --help` from the OAW root to inspect the
read-only export options. Export only the intended test database and review its
contents before sharing. The exporter does not independently establish scientific
acceptance. Keep test exports outside maintained README files.

The [modelling contract](modelling-contract.md) defines measurement coverage,
artifact validation and delivery gates. Model-reported pass values require
independent verification; a completed Run is not proof of physical validity.
