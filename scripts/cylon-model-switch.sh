#!/usr/bin/env bash
# Pick the model group that serves on spark1/spark2 — as a Git commit, never a kubectl edit.
# Runs ON ms02 (`just cylon-model-switch model=…` pipes it over ssh):
#   cylon-model-switch.sh flash-next | 27b | none
# Edits shared-gitops gitops/root/components/cylon-infra/serving/active-model.yaml in a
# throw-away worktree off origin/main (the ms02 clone may hold other people's work),
# commits to main, pushes, and reconciles Flux. The other group's weights stay on the Sparks.
set -euo pipefail
model="${1:-}"
case "${model}" in
  flash-next) want_27b=0 want_fn=1 ;;
  27b)        want_27b=1 want_fn=0 ;;
  none)       want_27b=0 want_fn=0 ;;
  *) echo "usage: $0 flash-next|27b|none" >&2; exit 2 ;;
esac
repo="${HOME}/Workspace/microscaler/shared-gitops-k8s-cluster"
wt="$(mktemp -d /tmp/cylon-model-switch.XXXXXX)"
git -C "${repo}" fetch -q origin
git -C "${repo}" worktree add -q --detach "${wt}" origin/main
trap 'git -C "${repo}" worktree remove --force "${wt}" >/dev/null 2>&1 || true' EXIT
f="${wt}/gitops/root/components/cylon-infra/serving/active-model.yaml"
python3 - "${f}" "${want_27b}" "${want_fn}" <<'PY'
import re, sys
path, r27, rfn = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
for name, want in (("qwen38-27b", r27), ("qwen38-flash-next", rfn)):
    s, n = re.subn(rf"(name: {name}\n  namespace: cylon\nspec:\n  replicas: )\d+", rf"\g<1>{want}", s)
    if n != 1:
        sys.exit(f"{path}: no replicas line for {name}")
open(path, "w").write(s)
PY
cd "${wt}"
if git diff --quiet; then
  echo "active-model.yaml already serves ${model}"
else
  msg="cylon: serve ${model} on spark1/spark2 (just cylon-model-switch)"
  # Optional trailer lines (e.g. co-author) from the caller's environment.
  [ -n "${CYLON_SWITCH_TRAILER:-}" ] && msg="${msg}"$'\n\n'"${CYLON_SWITCH_TRAILER}"
  git commit -q -am "${msg}"
  for _ in 1 2 3; do
    git pull -q --rebase origin main && git push -q origin HEAD:main && break
    sleep 3
  done
  git log --oneline -1
fi
flux reconcile source git shared-gitops -n flux-system
flux reconcile kustomization stack-cylon-serving -n flux-system
kubectl -n cylon get lws,pods -o wide
