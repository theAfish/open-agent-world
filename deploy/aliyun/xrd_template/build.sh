#!/usr/bin/env bash
# Image-build entrypoint; never run on the OAW application host.
set -euo pipefail
source_root=$(realpath "$1")
prefix=/opt/oaw-xrd
test ! -e "$prefix"
test -f "$source_root/plugins/xrd/src/oaw_xrd/worker_launcher.py"
export DEBIAN_FRONTEND=noninteractive
export PIP_DISABLE_PIP_VERSION_CHECK=1
export UV_NO_CACHE=1
export UV_PYTHON_INSTALL_DIR="$prefix/python"
export MPLBACKEND=Agg QT_QPA_PLATFORM=offscreen
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1

# Dependencies belong to the template, never the ECS interpreter or user Volume.
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git python3-venv \
    cmake ninja-build g++ gfortran qt6-base-dev libqt6sql6-sqlite
mkdir -p "$prefix"
python3 -m venv "$prefix/bootstrap"
"$prefix/bootstrap/bin/python" -m pip --isolated install --no-cache-dir \
    --index-url https://mirrors.aliyun.com/pypi/simple/ uv==0.12.7
uv="$prefix/bootstrap/bin/uv"
"$uv" python install 3.11.15
"$uv" venv --python 3.11.15 "$prefix/venv"
"$uv" pip install --python "$prefix/venv/bin/python" --only-binary=:all: --no-deps \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    -r "$source_root/deploy/aliyun/xrd_template/requirements.lock"
"$uv" pip check --python "$prefix/venv/bin/python"
cp "$source_root/deploy/aliyun/xrd_template/requirements.lock" "$prefix/requirements.lock"
cp -a "$source_root/plugins/xrd" "$prefix/pack"

# Same reviewed upstream and patch as the Pack's native QualX integration.
qualx_revision=ea66135eb2a8dabd60f11e24c316239d618c2f1a
git init "$prefix/qualx-source"
git -C "$prefix/qualx-source" fetch --depth 1 https://github.com/ccorrado71/qualx3.git "$qualx_revision"
git -C "$prefix/qualx-source" checkout --detach FETCH_HEAD
test "$(git -C "$prefix/qualx-source" rev-parse HEAD)" = "$qualx_revision"
# Windows checkouts may use CRLF; preserve the patch content with Linux endings.
sed 's/\r$//' "$prefix/pack/engines/qualx-1.0.5-oaw.patch" > "$prefix/qualx-source/oaw.patch"
git -C "$prefix/qualx-source" apply --check "$prefix/qualx-source/oaw.patch"
git -C "$prefix/qualx-source" apply "$prefix/qualx-source/oaw.patch"
cmake -S "$prefix/qualx-source" -B "$prefix/qualx-build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix/qualx" \
    -DBUILD_DEB_PACKAGE=ON -DBUILD_APPIMAGE=ON
cmake --build "$prefix/qualx-build" --parallel 4
cmake --install "$prefix/qualx-build"
mkdir -p "$prefix/qualx/share/licenses"
cp "$prefix/qualx-source/LICENSE" "$prefix/qualx/share/licenses/QualX-LICENSE"
cp "$prefix/pack/engines/qualx-1.0.5-oaw.patch" "$prefix/qualx/share/licenses/"
"$prefix/venv/bin/python" -I "$prefix/pack/src/oaw_xrd/verify_environment.py" --check "$prefix"
"$prefix/venv/bin/python" -I "$source_root/deploy/aliyun/xrd_template/verify.py" "$prefix"
chmod -R a-w "$prefix/pack" "$prefix/qualx"
# Keep compilers out of the snapshot; fixed, template-only paths.
rm -rf "$prefix/qualx-build" "$prefix/qualx-source/.git" "$prefix/bootstrap"
apt-get clean
mv "$prefix/verified.json" "$prefix/ready.json"
