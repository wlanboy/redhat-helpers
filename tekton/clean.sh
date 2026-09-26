#!/usr/bin/env bash
#
# clean.sh
#
# Löscht die PipelineRuns der Build-Pipelines aus diesem Ordner:
#   - postgres-build-publish-ubi9
#   - erlang-build-publish-ubi9
# Mit den PipelineRuns entfernt Kubernetes auch deren TaskRuns, Pods und die
# Workspace-PVCs aus volumeClaimTemplate. Andere Runs im Namespace bleiben.
# Laufende Runs werden dabei abgebrochen.
#
# Aufruf:
#   tekton/clean.sh              # beide Pipelines
#   tekton/clean.sh postgres     # nur postgres-build-publish-ubi9
#   tekton/clean.sh erlang       # nur erlang-build-publish-ubi9
#
# Namespace:  $NAMESPACE (Default: tekton)
# Kontext:    $KUBE_CONTEXT (Default: aktueller kubectl-Kontext)
# Ohne Nachfrage: DELETE=j

set -euo pipefail

NAMESPACE=${NAMESPACE:-tekton}

case "${1:-all}" in
    all)      PIPELINES="postgres-build-publish-ubi9,erlang-build-publish-ubi9" ;;
    postgres) PIPELINES="postgres-build-publish-ubi9" ;;
    erlang)   PIPELINES="erlang-build-publish-ubi9" ;;
    *)
        echo "Aufruf: $0 [all|postgres|erlang]" >&2
        exit 1
        ;;
esac

if ! command -v kubectl &>/dev/null; then
    echo "Fehler: 'kubectl' nicht gefunden." >&2
    exit 1
fi

CONTEXT=${KUBE_CONTEXT:-$(kubectl config current-context)}
K=(kubectl --context "$CONTEXT" -n "$NAMESPACE")
SELECTOR="tekton.dev/pipeline in (${PIPELINES})"

RUNS=$("${K[@]}" get pipelinerun -l "$SELECTOR" --no-headers 2>/dev/null || true)
if [[ -z "$RUNS" ]]; then
    echo "Keine PipelineRuns für ${PIPELINES} in ${CONTEXT}/${NAMESPACE}."
    exit 0
fi

echo "Kontext:   $CONTEXT"
echo "Namespace: $NAMESPACE"
echo
"${K[@]}" get pipelinerun -l "$SELECTOR"
echo

DO_DELETE=${DELETE:-}
if [[ -z "$DO_DELETE" ]]; then
    read -rp "Diese $(wc -l <<< "$RUNS") PipelineRun(s) löschen? [j/N]: " DO_DELETE || true
fi
if [[ ! "$DO_DELETE" =~ ^[JjYy]$ ]]; then
    echo "Abgebrochen."
    exit 0
fi

"${K[@]}" delete pipelinerun -l "$SELECTOR"
echo "Fertig."
