#!/usr/bin/env bash
# Run in an MSYS2 UCRT64 shell. All dependencies and output paths are explicit.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: build.sh --source DIR --build DIR --toolchain DIR --install DIR [--jobs N] [--python EXE]

  --source      QualX source with qualx-1.0.5-oaw.patch already applied
  --build       Out-of-tree CMake build directory
  --toolchain   MSYS2 UCRT64 prefix containing bin/cmake.exe and bin/gfortran.exe
  --install     Portable engine destination (bin/qualx.exe, share/, runtime DLLs)
  --jobs        Parallel build jobs (default: 4)
  --python      Native Windows Python executable (default: TOOLCHAIN/bin/python.exe)
EOF
}

source_dir= build_dir= toolchain_dir= install_dir= python_exe= jobs=4
while (($#)); do
  case "$1" in
    --source|--build|--toolchain|--install|--jobs|--python)
      if (($# < 2)) || [[ -z "$2" ]]; then
        printf 'Missing value for %s\n' "$1" >&2
        exit 2
      fi
      case "$1" in
        --source) source_dir="$2" ;;
        --build) build_dir="$2" ;;
        --toolchain) toolchain_dir="$2" ;;
        --install) install_dir="$2" ;;
        --jobs) jobs="$2" ;;
        --python) python_exe="$2" ;;
      esac
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$source_dir" || -z "$build_dir" || -z "$toolchain_dir" || -z "$install_dir" ]]; then
  usage >&2
  exit 2
fi
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
  printf '%s\n' '--jobs must be a positive integer.' >&2
  exit 2
fi
source_dir="$(realpath "$source_dir")"
toolchain_dir="$(realpath "$toolchain_dir")"
mkdir -p "$build_dir" "$install_dir"
build_dir="$(realpath "$build_dir")"
install_dir="$(realpath "$install_dir")"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_exe="${python_exe:-$toolchain_dir/bin/python.exe}"

[[ -f "$source_dir/CMakeLists.txt" ]] || { printf '%s\n' 'QualX CMakeLists.txt not found.' >&2; exit 2; }
for tool in cmake.exe ninja.exe gcc.exe g++.exe gfortran.exe objdump.exe; do
  [[ -x "$toolchain_dir/bin/$tool" ]] || { printf 'Toolchain executable missing: %s\n' "$tool" >&2; exit 2; }
done

# This environment is private to this build process; no system PATH is changed.
export PATH="$toolchain_dir/bin:$PATH"
"$toolchain_dir/bin/cmake.exe" -S "$source_dir" -B "$build_dir" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="$toolchain_dir/bin/g++.exe" \
  -DCMAKE_Fortran_COMPILER="$toolchain_dir/bin/gfortran.exe" \
  -DCMAKE_MAKE_PROGRAM="$toolchain_dir/bin/ninja.exe" \
  -DCMAKE_PREFIX_PATH="$toolchain_dir" \
  -DCMAKE_INSTALL_PREFIX="$install_dir"
"$toolchain_dir/bin/cmake.exe" --build "$build_dir" --parallel "$jobs"
"$toolchain_dir/bin/cmake.exe" --install "$build_dir"
"$python_exe" "$script_dir/deploy_runtime.py" --toolchain "$toolchain_dir" --install "$install_dir"
"$install_dir/bin/qualx.exe" --nogui --help
