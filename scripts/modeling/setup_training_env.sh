#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-model"
VENDOR_DIR="${VENV_DIR}/vendor/llvm-openmp"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OPENMP_PACKAGE="llvm-openmp-15.0.5-h7cfbb63_0.tar.bz2"
OPENMP_URL="https://conda.anaconda.org/conda-forge/osx-arm64/${OPENMP_PACKAGE}"
XGBOOST_LIB_GLOB="${VENV_DIR}"/lib/python*/site-packages/xgboost/lib/libxgboost.dylib
LOCAL_RPATH='@loader_path/../../../../../vendor/llvm-openmp/lib'

cd "${ROOT_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip >/dev/null
"${VENV_DIR}/bin/python" -m pip install -r scripts/modeling/requirements.txt >/dev/null

mkdir -p "${VENDOR_DIR}"
if [[ ! -f "${VENDOR_DIR}/lib/libomp.dylib" ]]; then
  curl -L --fail --output "${VENDOR_DIR}/${OPENMP_PACKAGE}" "${OPENMP_URL}"
  tar -xjf "${VENDOR_DIR}/${OPENMP_PACKAGE}" -C "${VENDOR_DIR}"
fi

shopt -s nullglob
for dylib in ${XGBOOST_LIB_GLOB}; do
  if ! otool -l "${dylib}" | grep -Fq "${LOCAL_RPATH}"; then
    install_name_tool -add_rpath "${LOCAL_RPATH}" "${dylib}"
  fi
done

echo "Model environment is ready at ${VENV_DIR}"
echo "Local OpenMP runtime: ${VENDOR_DIR}/lib/libomp.dylib"
