#!/usr/bin/env bash
# No-egress test for the COMPOSE profile (spec F21, BUILD_PROMPT Phase 4 task 7).
# From every running kit container, try DNS, HTTPS to a public IP, and raw TCP. Any success fails.
#
#   deploy/security/no_egress_compose.sh            # test the running stack
#   deploy/security/no_egress_compose.sh --self-test # prove the test catches a leak: attaches a
#                                                    # throwaway container to an outbound network
#                                                    # and EXPECTS a failure
# Needs: docker, the stack running (docker compose -f deploy/docker-compose.yml up -d).
# Status: written, NOT YET RUN (the dev laptop's Docker engine was unavailable).
set -uo pipefail
PROJECT="${COMPOSE_PROJECT_NAME:-airgap}"
TARGET_IP="1.1.1.1"
fail=0

probe() {  # container -> prints LEAK lines for anything that got out
  local c="$1"
  # Each probe uses whatever tool the image has; a missing tool counts as "could not test".
  docker exec "$c" sh -c "
    (command -v getent >/dev/null && getent hosts example.com >/dev/null 2>&1 && echo 'LEAK dns') ;
    (command -v wget >/dev/null && timeout 5 wget -q -O /dev/null --no-check-certificate https://$TARGET_IP 2>/dev/null && echo 'LEAK https') ;
    (command -v python3 >/dev/null && timeout 5 python3 -c 'import socket;socket.create_connection((\"$TARGET_IP\",443),3)' 2>/dev/null && echo 'LEAK tcp') ;
    (command -v bash >/dev/null && timeout 5 bash -c ': > /dev/tcp/$TARGET_IP/443' 2>/dev/null && echo 'LEAK bash-tcp') ;
    true" 2>/dev/null
}

if [[ "${1:-}" == "--self-test" ]]; then
  net="airgap-egress-selftest"
  docker network create "$net" >/dev/null
  docker run -d --rm --name airgap-egress-probe --network "$net" python:3.12.16-slim-bookworm sleep 60 >/dev/null
  out="$(probe airgap-egress-probe)"
  docker rm -f airgap-egress-probe >/dev/null; docker network rm "$net" >/dev/null
  if [[ -n "$out" ]]; then echo "SELF-TEST OK: leak detected as expected ($out)"; exit 0; fi
  echo "SELF-TEST FAILED: the probe could not detect an open outbound network"; exit 1
fi

containers="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}')"
[[ -z "$containers" ]] && { echo "no running containers for project $PROJECT"; exit 2; }
for c in $containers; do
  out="$(probe "$c")"
  if [[ -n "$out" ]]; then echo "FAIL $c: $out"; fail=1; else echo "ok   $c"; fi
done
exit $fail
