#!/usr/bin/env bash
set -Eeuo pipefail

readonly ACTION="${1:?usage: deploy_backend_analysis_worker.sh deploy|rollback <40-char-release-sha>}"
readonly RELEASE_ID="${2:?usage: deploy_backend_analysis_worker.sh deploy|rollback <40-char-release-sha>}"
if [[ "${ACTION}" != "deploy" && "${ACTION}" != "rollback" ]]; then
  echo "Action must be deploy or rollback." >&2
  exit 2
fi
if [[ ! "${RELEASE_ID}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Release id must be a 40-character Git commit SHA." >&2
  exit 2
fi
if [[ "${AI_ANALYSIS_ROLLOUT_GATE:-}" != "quiesced-request-v4-result-v3" ]]; then
  echo "Coordinated analysis rollout gate is not satisfied." >&2
  exit 2
fi

readonly REPOSITORY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly COMPOSE_FILE="${REPOSITORY_ROOT}/deploy/backend-analysis-worker/compose.yaml"
readonly PROJECT_NAME="${AI_ANALYSIS_COMPOSE_PROJECT_NAME:-voice-coach-analysis}"
readonly IMAGE_REPOSITORY="${AI_ANALYSIS_WORKER_IMAGE_REPOSITORY:-intelligentai/backend-analysis-worker}"
readonly RELEASE_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_ID}"
readonly WAIT_TIMEOUT_SECONDS="${AI_ANALYSIS_DEPLOY_WAIT_TIMEOUT_SECONDS:-600}"
if [[ ! "${WAIT_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] \
  || (( WAIT_TIMEOUT_SECONDS < 60 || WAIT_TIMEOUT_SECONDS > 1800 )); then
  echo "Deploy wait timeout must be between 60 and 1800 seconds." >&2
  exit 2
fi

compose_with_image() {
  local image="$1"
  shift
  AI_ANALYSIS_WORKER_IMAGE="${image}" docker compose \
    --project-name "${PROJECT_NAME}" \
    --file "${COMPOSE_FILE}" \
    "$@"
}

activate_image() {
  local image="$1"
  compose_with_image "${image}" config --quiet
  compose_with_image "${image}" up \
    --detach \
    --no-deps \
    --wait \
    --wait-timeout "${WAIT_TIMEOUT_SECONDS}" \
    backend-analysis-worker
}

image_revision() {
  docker image inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$1"
}

if [[ "${ACTION}" == "rollback" ]]; then
  if [[ "$(image_revision "${RELEASE_IMAGE}")" != "${RELEASE_ID}" ]]; then
    echo "Rollback image revision does not match the requested release." >&2
    exit 2
  fi
  activate_image "${RELEASE_IMAGE}"
  echo "Worker rollback release ${RELEASE_ID} is healthy."
  exit 0
fi

readonly CHECKED_OUT_RELEASE="$(git -C "${REPOSITORY_ROOT}" rev-parse --verify HEAD)"
if [[ "${CHECKED_OUT_RELEASE}" != "${RELEASE_ID}" ]]; then
  echo "Checked-out revision does not match the requested release." >&2
  exit 2
fi
if [[ -n "$(git -C "${REPOSITORY_ROOT}" status --porcelain --untracked-files=normal)" ]]; then
  echo "Worker release builds require a clean worktree." >&2
  exit 2
fi

readonly CURRENT_CONTAINER="$(
  compose_with_image "${RELEASE_IMAGE}" ps --quiet backend-analysis-worker 2>/dev/null || true
)"
previous_image_id=""
rollback_image=""
if [[ -n "${CURRENT_CONTAINER}" ]]; then
  previous_image_id="$(docker inspect --format '{{.Image}}' "${CURRENT_CONTAINER}")"
  rollback_image="${IMAGE_REPOSITORY}:rollback-before-${RELEASE_ID}"
  docker image tag "${previous_image_id}" "${rollback_image}"
fi

rollback_failed_deploy() {
  local original_status=$?
  trap - ERR
  if [[ -n "${rollback_image}" ]]; then
    echo "Worker deployment failed; restoring the previous image." >&2
    if activate_image "${rollback_image}"; then
      echo "Previous worker image is healthy after rollback." >&2
    else
      echo "Previous worker image rollback did not become healthy." >&2
    fi
  else
    echo "Worker deployment failed and no previous container was available." >&2
  fi
  return "${original_status}"
}
trap rollback_failed_deploy ERR

docker build \
  --file "${COMPOSE_FILE%/*}/Dockerfile" \
  --build-arg "VCS_REF=${RELEASE_ID}" \
  --tag "${RELEASE_IMAGE}" \
  "${REPOSITORY_ROOT}"

actual_revision="$(image_revision "${RELEASE_IMAGE}")"
test "${actual_revision}" = "${RELEASE_ID}"

activate_image "${RELEASE_IMAGE}"
trap - ERR
echo "Worker release ${RELEASE_ID} is healthy."
