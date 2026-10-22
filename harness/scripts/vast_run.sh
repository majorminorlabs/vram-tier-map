#!/usr/bin/env bash
set -euo pipefail
cat >&2 <<'EOF'
vast_run.sh is an integration point. Add provider-specific provisioning only
after pinning the image, GPU selection, price cap, and teardown behavior.
This starter intentionally does not create paid instances.
EOF
exit 2
