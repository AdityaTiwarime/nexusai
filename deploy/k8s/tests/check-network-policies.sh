#!/usr/bin/env bash
# Proves the network policies are enforced, not just applied: a pod labelled as the
# worker can reach PostgreSQL and Redis, an unlabelled pod in the same namespace can't,
# and nothing but the frontend can call the API directly.
# Uses the backend image (already loaded into kind) as a tiny TCP client.
set -euo pipefail
NS="${NAMESPACE:-nexusai}"
IMAGE="${PROBE_IMAGE:-nexusai-backend:ci}"

probe() { # probe <name> <component-label or "none"> <host> <port>
  local name="$1" component="$2" host="$3" port="$4" labels=""
  [[ "$component" != none ]] && labels="app.kubernetes.io/component: $component"
  kubectl -n "$NS" delete pod "$name" --ignore-not-found --wait=true >/dev/null
  kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: $name
  labels:
    probe: "true"
    $labels
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    seccompProfile: { type: RuntimeDefault }
  containers:
    - name: probe
      image: $IMAGE
      command: ["python", "-c", "import socket; socket.create_connection(('$host', $port), timeout=5); print('connected')"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: { drop: [ALL] }
YAML
  for _ in $(seq 1 60); do
    phase="$(kubectl -n "$NS" get pod "$name" -o jsonpath='{.status.phase}')"
    [[ "$phase" == Succeeded || "$phase" == Failed ]] && break
    sleep 2
  done
  kubectl -n "$NS" delete pod "$name" --wait=false >/dev/null
  echo "$phase"
}

expect() { # expect <Succeeded|Failed> <description> <probe args...>
  local want="$1" what="$2"
  shift 2
  local got
  got="$(probe "$@")"
  if [[ "$got" == "$want" ]]; then
    echo "ok: $what"
  else
    echo "FAIL: $what (pod phase $got, expected $want)"
    exit 1
  fi
}

expect Succeeded "worker pod can reach PostgreSQL" np-worker-db worker postgres 5432
expect Succeeded "worker pod can reach Redis" np-worker-redis worker redis 6379
expect Failed "unlabelled pod can't reach PostgreSQL" np-anon-db none postgres 5432
expect Failed "unlabelled pod can't reach Redis" np-anon-redis none redis 6379
expect Failed "unlabelled pod can't call the API directly" np-anon-api none backend 8000
expect Failed "worker pod can't call the API (only the frontend can)" np-worker-api worker backend 8000
