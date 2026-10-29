# Mixed Python and visual card

This standalone Schema 3 source project calculates an orthogonal unit-cell
volume. Python validates the dimensions and performs the calculation. The card's
fields, button, layout and colors live in `designs/card.json`. No Python is
generated when that resource is edited.

Open this directory using **Pack & Card Library → Developer tools → Pack IDE (Experimental) → Open project**.
Select **Cell Analysis**, change its appearance in Design, and save. Function
binds the visible button to the handwritten `analyze_structure` resource action;
the result component reads its `result` response key. The `multiply` setting is
only used by the declarative behavior compiler for cards without Python hooks;
it does not replace this project's Python handler.

With an OAW checkout/interpreter supporting Plugin API 1.39, build the Python
wheel in your external editor or an authorized development environment:

```sh
uv build --wheel --out-dir dist/backend
python /path/to/OAW/scripts/oaw-pack.py doctor .
python /path/to/OAW/scripts/oaw-pack.py build-project . dist/analysis-0.1.0.oawpack \
  --backend-wheel dist/backend/oaw_pack_example_analysis_card-0.1.0-py3-none-any.whl
python /path/to/OAW/scripts/oaw-pack.py inspect dist/analysis-0.1.0.oawpack
```

Use the OAW interpreter for the `python` commands. In the IDE's build inputs,
set Backend wheel to
`dist/backend/oaw_pack_example_analysis_card-0.1.0-py3-none-any.whl`; Build / Export
packages that same existing wheel. Install the archive through the
ordinary Pack installer into a disposable profile, then restart that host to
activate the Python adapter. On the installed card, set the lengths to 3, 5 and 7
and click **Analyze structure**: the result is `Orthogonal cell volume: 105 A^3`.
Zero, negative and nonfinite lengths fail validation in the real Python handler.

Static IDE inspection, visual preview and packaging do not import the project.
Visual preview alone does not establish that Python is installed or executable.
Only run code you trust in a disposable profile or authorized sandbox. Advanced
execution jobs and Agent capability grants use the existing Plugin API; this
small example does not grant Agent execution permission.

To distribute a new Pack, change its identity consistently in `oaw.pack.toml`,
`pyproject.toml` and the Python registration, and choose your own authorized
Marketplace namespace. The supplied `example.*` identity is for local tests.
