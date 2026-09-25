# QualX3 external search engine

This directory contains an integration patch and Windows build helpers, **not**
QualX source, binaries, or diffraction databases. OAW runs the separately built
engine as a subprocess, then uses its candidate IDs for OAW's own reranking.
The scores shown by that pipeline are not exported QualX FOM scores.

## Source and patch

- Upstream: <https://github.com/ccorrado71/qualx3>
- Version: `v1.0.5`
- Pinned source commit: `ea66135eb2a8dabd60f11e24c316239d618c2f1a`
- Patch: [`qualx-1.0.5-oaw.patch`](qualx-1.0.5-oaw.patch)
- Patch SHA256: `297bd3e05c7557215a4280c2871257dde96eb0e1b3fd6b4824affa5042fa035a`

The patch adds explicit database selection, per-run INI settings, read-only
database access, a working wavelength override, and nonzero error exits for
failed pattern loading or a peak search that finds no peaks. It leaves the
upstream search/match algorithm and text candidate format in place.

## Build on Windows without a system installation

Use a portable [MSYS2](https://www.msys2.org/) UCRT64 environment in a local
build directory. The validated build used the official
[`msys2-base-x86_64-20260611.tar.xz`](https://repo.msys2.org/distrib/x86_64/msys2-base-x86_64-20260611.tar.xz)
base archive, GCC/GFortran 16.2.0, Qt 6.11.2, CMake 4.4.3, and Ninja 1.13.2.
[`package-versions.txt`](package-versions.txt) records the complete installed
package set for that build; it is provenance, not a package-manager lockfile.

Set `MSYSTEM=UCRT64` and `CHERE_INVOKING=1` for the portable MSYS2 shell process.
Do not add this toolchain to the machine's persistent PATH. Inside that shell,
install the build dependencies into its own UCRT64 prefix:

```sh
pacman -Sy --noconfirm --needed \
  mingw-w64-ucrt-x86_64-gcc-fortran \
  mingw-w64-ucrt-x86_64-qt6-base \
  mingw-w64-ucrt-x86_64-cmake \
  mingw-w64-ucrt-x86_64-ninja
```

The following example uses MSYS-style paths; replace them with your local paths.
Keep the vendor source, build output, and installed engine outside the OAW
checkout so that they are not included in a plugin commit.

```sh
repo=/c/path/to/open-agent-world-library-xrd
scratch=/c/path/to/qualx-build
toolchain=/c/path/to/portable-msys64/ucrt64
mkdir -p "$scratch"

git clone --depth 1 --branch v1.0.5 \
  https://github.com/ccorrado71/qualx3.git "$scratch/source"
git -C "$scratch/source" checkout --detach ea66135eb2a8dabd60f11e24c316239d618c2f1a
git -C "$scratch/source" apply --check "$repo/plugins/xrd/engines/qualx-1.0.5-oaw.patch"
git -C "$scratch/source" apply "$repo/plugins/xrd/engines/qualx-1.0.5-oaw.patch"

bash "$repo/plugins/xrd/engines/build.sh" \
  --source "$scratch/source" \
  --build "$scratch/build" \
  --toolchain "$toolchain" \
  --install "$scratch/installed" \
  --jobs 4
```

`build.sh` configures, compiles, installs, invokes `deploy_runtime.py`, then
checks `qualx.exe --nogui --help`. Use `--python` to select another native
Windows Python interpreter; by default it uses the toolchain's `bin/python.exe`.
It changes environment variables only in the build process.

The extra runtime deployment step is necessary: Qt's generated installation
script does not copy every GCC/Fortran dependency. To run it separately:

```sh
"$toolchain/bin/python.exe" "$repo/plugins/xrd/engines/deploy_runtime.py" \
  --toolchain "$toolchain" --install "$scratch/installed"
```

The deployment helper follows imported DLLs recursively and copies dependencies
only from the selected toolchain. It does not copy Windows system DLLs. Its
`runtime-deployment.json` also lists imports absent from the toolchain, including
normal Windows APIs and optional Qt database drivers; that list is not a claim
that every optional driver has been installed. The OAW path uses Qt's SQLite
driver.

## Install for OAW

Copy the **entire** installed directory into
`<OAW_XRD_ROOT>/engines/qualx3`, preserving this layout:

```text
engines/qualx3/
  bin/qualx.exe
  bin/qt.conf
  bin/*.dll
  share/qualx/
  share/qt6/plugins/
```

Alternatively, set `OAW_XRD_QUALX_EXECUTABLE` to the installed executable. The
rest of its portable directory must remain alongside it. The unmodified stock
1.0.5 executable does not implement the required isolation arguments and should
not be configured as this engine.

## CLI contract

```text
qualx.exe --nogui --search INPUT.xy --settings-dir RUN_SETTINGS_DIR --database FULL_PATH.sq --wavelength 1.540593
```

For an allowed-element filter, append:

```text
--composition "Li AND Ti AND P AND O" --contains-any
```

- `--settings-dir` and `--database` must be supplied together, require `--nogui`
  and `--search`, and cannot be combined with `--createdb`.
- The database argument includes the `.sq` suffix. Its adjacent `.sq.info`,
  `.sq.infostat`, and `.sq.search` files must also exist. All four are opened
  with `QSQLITE_OPEN_READONLY`.
- Isolated runs bypass the user's database list and automatic discovery.
  Default `QSettings` uses INI files below the supplied directory, including its
  system fallback path. The application-specific file is `IC/qualx.ini`.
  Supply a fresh directory for each run; do not reuse a GUI settings profile.
- `--wavelength` requires a finite value in `(0, 100]` Angstrom. It overrides the
  loaded 2theta spectrum's wavelength before peak extraction and matching. OAW
  also includes the same wavelength in the generated XY header. Stock 1.0.5's
  `ProgOptions.wavel` field was not consumed by the Fortran implementation.
- Candidate IDs remain in the upstream stdout lines beginning with `[ID]`.
  Their stdout order is not guaranteed to be a FOM ranking; FOM is not exported.
- `--help` lists the three added flags, which OAW uses to check compatibility.
- Failed pattern loading and failure to find peaks return a nonzero status.

## Checks performed on the validated build

The Windows build passed help/version probes, rejection of an unpaired settings
argument, and real-spectrum searches both with and without an element filter.
For the CQ250703-1 spectrum and the Li/Ti/P/O filter, candidate COD `7222155` was
returned. Across those probes the recursive HKCU `Software/IC` fingerprint did
not change, and the main database plus its three companions retained size and
nanosecond modification time. These are integration checks, not a phase-ID
accuracy benchmark.

The locally validated executable SHA256 was
`057d85ca7ee21a1998260586a6001958f0e20811c5d0d030809f1cd951d4eb6e`.
Rebuilding can produce a different hash because the upstream executable embeds
build time and compiler information.

## Upstream licensing information

At the pinned commit, upstream `README.md` states
“GNU Lesser General Public License v3.0 (LGPL-3.0)”, while its root `LICENSE`
starts “GNU GENERAL PUBLIC LICENSE” and “Version 3, 29 June 2007”. This repository
records that inconsistency rather than silently selecting or changing the
upstream license:

- [Upstream README at the pinned commit](https://github.com/ccorrado71/qualx3/blob/ea66135eb2a8dabd60f11e24c316239d618c2f1a/README.md#license)
- [Upstream LICENSE at the pinned commit](https://github.com/ccorrado71/qualx3/blob/ea66135eb2a8dabd60f11e24c316239d618c2f1a/LICENSE)

Keep the upstream license and provenance with external source/build artifacts.
Do not relabel QualX code as OAW-licensed. Qt, GCC runtime, and other portable
dependencies retain their respective licenses. No upstream source or compiled
runtime is vendored in this plugin directory.

## PyWPEM bounded cell updates

Apply `pywpem-bounded-cell.patch` to the local PyWPEM source after the live-frame patch.
It corrects the triclinic metric determinant sign and replaces unconstrained
Newton cell steps with symmetry-preserving bounded least squares on Bragg angles.
Each step allows at most 1% length change and 0.5 degree angle change; these are
per-step limits, not global bounds relative to the initial CIF. Invalid metrics,
Bragg-domain violations, failed optimization and worsening steps retain the previous
cell. Fewer selected reflections than cell parameters leave the cell unchanged.
The engine source hashes in each review include this implementation, invalidating
old fit caches. This changes the post-search refinement protocol, not the LLM/BO score.

Zero-responsibility EM peaks retain their previous centers rather than generating
0/0. Nonfinite responsibilities still fail explicitly. Run the independent metric
and optimization tests with the scientific Python and `OAW_XRD_ROOT` set:
`python plugins/xrd/engines/test_pywpem_bounded_cell.py -q`.

Per-phase export now consumes each coincident reflection once within its own phase,
preventing duplicate peak centers from reusing another component's parameters.
The adapter skips exactly zero-area exports with undefined mixing ratios and
continues to reject invalid nonzero components or failed profile closure.
