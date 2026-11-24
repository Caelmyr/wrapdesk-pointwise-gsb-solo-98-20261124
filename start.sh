#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == "stop" ]]; then
  exec docker compose down
fi
if [[ -z "${WRAPDESK_RUNTIME_IMAGE:-}" ]] && docker image inspect gasketdesk-runtime-local:verified >/dev/null 2>&1; then
  export WRAPDESK_RUNTIME_IMAGE=gasketdesk-runtime-local:verified
  export DOCKER_BUILDKIT=0 BUILDX_BUILDER=default
fi
docker compose up -d --build --wait
printf 'WrapDesk http://127.0.0.1:%s/\n' "${WRAPDESK_PORT:-19901}"
