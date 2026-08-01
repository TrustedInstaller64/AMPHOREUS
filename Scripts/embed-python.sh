#!/bin/bash
# Xcode Run Script Build Phase
# Copies Python.framework and δ-me13 project into the .app bundle

set -e

APP_DIR="${BUILT_PRODUCTS_DIR}/${WRAPPER_NAME}"
FW_DIR="${APP_DIR}/Contents/Frameworks"
RES_DIR="${APP_DIR}/Contents/Resources"

echo "Embedding Python.framework → ${FW_DIR}"
mkdir -p "${FW_DIR}"

# Copy Python.framework (includes stdlib + site-packages)
SYSTEM_PY="/Library/Frameworks/Python.framework"
if [ -d "${SYSTEM_PY}" ]; then
    if [ ! -d "${FW_DIR}/Python.framework" ]; then
        cp -R "${SYSTEM_PY}" "${FW_DIR}/Python.framework"
    fi
    echo "  Python.framework copied."
else
    echo "  WARNING: ${SYSTEM_PY} not found, skipping."
fi

# Fix rpath so the framework finds itself in the bundle
install_name_tool -change \
    "/Library/Frameworks/Python.framework/Versions/3.12/Python" \
    "@executable_path/../Frameworks/Python.framework/Versions/3.12/Python" \
    "${FW_DIR}/Python.framework/Versions/3.12/bin/python3" 2>/dev/null || true

# δ-me13 is auto-synced by Xcode's PBXFileSystemSynchronizedRootGroup.
# Clean up duplicate .pth files from old runs/ directories.
if [ -d "${RES_DIR}/runs" ]; then
    rm -rf "${RES_DIR}/runs"
    echo "  Removed old runs/ from Resources."
fi

echo "Embed complete."
