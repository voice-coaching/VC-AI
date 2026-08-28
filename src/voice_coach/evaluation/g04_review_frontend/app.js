"use strict";

const API_PREFIX = "/api/v1";
const TOKEN_STORAGE_KEY = "voice-coach.g04-review-token.v1";
const T43_PROFILE = "t43_root_cause";

const RESOLUTION_OPTIONS = {
  alignment_algorithm: [
    ["reverified_after_alignment_fix", "정렬기 수정 후 재검증"],
    ["excluded_predeclared_qc", "사전 정의 QC 제외"],
  ],
  target_annotation: [
    ["reverified_after_annotation_fix", "주석 수정 후 재검증"],
    ["excluded_predeclared_qc", "사전 정의 QC 제외"],
  ],
  realized_annotation: [
    ["reverified_after_annotation_fix", "주석 수정 후 재검증"],
    ["excluded_predeclared_qc", "사전 정의 QC 제외"],
  ],
  both_annotations: [
    ["reverified_after_annotation_fix", "주석 수정 후 재검증"],
    ["excluded_predeclared_qc", "사전 정의 QC 제외"],
  ],
  audio_unusable: [["excluded_predeclared_qc", "사전 정의 QC 제외"]],
  no_defect_confirmed: [["retained_no_defect", "결함 없이 유지"]],
  other: [
    ["other_resolved", "기타 해결"],
    ["excluded_predeclared_qc", "사전 정의 QC 제외"],
  ],
};

const state = {
  token: "",
  profile: "",
  items: [],
  filter: "all",
  currentItemHash: null,
  currentItem: null,
  audioObjectUrl: null,
};

const element = (id) => document.getElementById(id);

function resolveAccessToken() {
  const fragmentToken = new URLSearchParams(window.location.hash.slice(1)).get("token");
  if (fragmentToken) {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, fragmentToken);
    window.history.replaceState(null, "", window.location.pathname);
    return fragmentToken;
  }
  return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
}

async function requestJson(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Review-Token", state.token);
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  return response.json();
}

async function responseError(response) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item.msg || "입력 검증 오류").join(" · ");
    }
  } catch (_error) {
    // Non-JSON server errors fall through to the status-based message.
  }
  return `서버 요청 실패 (${response.status})`;
}

function setFatalMessage(message) {
  const target = element("fatal-message");
  target.textContent = message;
  target.hidden = false;
  element("connection-state").textContent = "연결 실패";
  element("connection-state").classList.remove("is-online");
}

function setFormMessage(message, isError = false) {
  const target = element("form-message");
  target.textContent = message;
  target.classList.toggle("message-error", isError);
  target.hidden = !message;
}

function configureProfile(session) {
  state.profile = session.profile;
  const isT43 = state.profile === T43_PROFILE;
  element("profile-badge").textContent = isT43 ? "T43 원인 감사" : "I30 phone 감사";
  element("decision-heading").textContent = isT43
    ? "T43 원인·조치 판정"
    : "I30 phone-level 판정";
  element("profile-guidance").textContent = isT43
    ? "T43은 문제 후보로 풍부화된 표본입니다. 정확도 분모로 사용하지 않으며, 정렬·주석 원인과 해결 상태를 각각 확정합니다."
    : "I30은 독립 표본입니다. 잘못 대응된 phone interval 수를 정확히 기록하며 annotation fidelity나 root cause는 수집하지 않습니다.";
  for (const target of document.querySelectorAll(".t43-only")) {
    target.hidden = !isT43;
  }
  for (const targetId of ["annotation-fidelity", "root-cause", "resolution"]) {
    element(targetId).required = isT43;
  }
}

function updateSession(session) {
  configureProfile(session);
  const completed = session.completed_count;
  const expected = session.expected_count;
  const percentage = expected === 0 ? 0 : (completed / expected) * 100;
  element("progress-label").textContent = `${completed} / ${expected}`;
  element("remaining-label").textContent = `남은 항목 ${session.remaining_count}건`;
  element("progress-bar").style.width = `${percentage}%`;
  element("reviewer-hash").textContent = `reviewer · ${session.reviewer_id_sha256.slice(0, 16)}…`;
  element("reviewer-hash").title = session.reviewer_id_sha256;
  element("connection-state").textContent = "서버 연결됨";
  element("connection-state").classList.add("is-online");
}

function matchesCurrentFilter(item) {
  if (state.filter === "pending" && item.reviewed) {
    return false;
  }
  if (state.filter === "reviewed" && !item.reviewed) {
    return false;
  }
  const query = element("item-search").value.trim().toLowerCase();
  if (!query) {
    return true;
  }
  return String(item.audit_index).includes(query) || item.item_sha256.includes(query);
}

function metadataSummary(item) {
  const errorState = item.error_presence ? "error+" : "error−";
  return `${item.duration_seconds.toFixed(1)}s · ${errorState}`;
}

function renderItemList() {
  const container = element("item-list");
  container.replaceChildren();
  const filteredItems = state.items.filter(matchesCurrentFilter);
  element("empty-list").hidden = filteredItems.length !== 0;

  for (const item of filteredItems) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "item-button";
    button.dataset.itemHash = item.item_sha256;
    button.setAttribute("role", "listitem");
    button.classList.toggle("is-current", item.item_sha256 === state.currentItemHash);
    button.addEventListener("click", () => loadItem(item.item_sha256));

    const index = document.createElement("span");
    index.className = "item-index";
    index.textContent = item.audit_index;

    const main = document.createElement("span");
    main.className = "item-main";
    const hash = document.createElement("strong");
    hash.textContent = item.item_sha256.slice(0, 14);
    const metadata = document.createElement("small");
    metadata.textContent = metadataSummary(item);
    main.append(hash, metadata);

    const check = document.createElement("span");
    check.className = "item-check";
    check.textContent = item.reviewed ? "✓" : "";
    check.setAttribute("aria-label", item.reviewed ? "검수 완료" : "검수 미완료");

    button.append(index, main, check);
    container.append(button);
  }
}

function appendMetadataChip(label, value) {
  const chip = document.createElement("span");
  chip.className = "metadata-chip";
  chip.textContent = `${label} ${value}`;
  element("metadata-chips").append(chip);
}

function formatSeconds(value) {
  return typeof value === "number" ? value.toFixed(3) : "—";
}

function renderAlignmentTable(alignments) {
  const body = element("alignment-table");
  body.replaceChildren();
  alignments.forEach((alignment, index) => {
    const row = document.createElement("tr");
    const values = [
      index + 1,
      alignment.target_label ?? "—",
      alignment.realized_label ?? "—",
      `${formatSeconds(alignment.start)}–${formatSeconds(alignment.end)}`,
      alignment.operation ?? "—",
    ];
    values.forEach((value, valueIndex) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (valueIndex === 4 && value !== "match") {
        cell.className = "operation-mismatch";
      }
      row.append(cell);
    });
    body.append(row);
  });
}

function renderIntervals(containerId, intervals) {
  const container = element(containerId);
  container.replaceChildren();
  intervals.forEach((interval, index) => {
    const chip = document.createElement("span");
    chip.className = "interval-chip";
    const label = document.createElement("strong");
    label.textContent = interval.label ?? "—";
    chip.append(`${index + 1} · `, label, ` ${formatSeconds(interval.start)}–${formatSeconds(interval.end)}`);
    container.append(chip);
  });
}

function updateResolutionOptions(rootCause, selectedValue = "") {
  const select = element("resolution");
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = rootCause ? "해결 상태를 선택하세요" : "원인을 먼저 선택하세요";
  select.append(placeholder);
  const options = rootCause
    ? [...(RESOLUTION_OPTIONS[rootCause] || []), ["unresolved", "미해결"]]
    : [];
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
  select.value = selectedValue;
}

function resetForm(review, alignmentCount) {
  element("alignment-correct").value = review
    ? String(review.alignment_correct)
    : "";
  element("incorrect-count").value = review?.incorrect_interval_count ?? "";
  element("incorrect-count").max = alignmentCount;
  element("boundary-error").value = review?.maximum_boundary_error_ms ?? "";
  element("annotation-fidelity").value = review?.annotation_fidelity ?? "";
  element("root-cause").value = review?.root_cause ?? "";
  updateResolutionOptions(review?.root_cause ?? "", review?.resolution ?? "");
  element("review-notes").value = review?.notes ?? "";
  element("note-count").textContent = element("review-notes").value.length;
  setFormMessage("");
}

async function loadAudio(itemHash) {
  element("audio-state").textContent = "불러오는 중";
  const response = await fetch(
    `${API_PREFIX}/review-items/${encodeURIComponent(itemHash)}/audio`,
    {headers: {"X-Review-Token": state.token}},
  );
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  if (state.audioObjectUrl) {
    URL.revokeObjectURL(state.audioObjectUrl);
  }
  state.audioObjectUrl = URL.createObjectURL(await response.blob());
  element("audio-player").src = state.audioObjectUrl;
  element("audio-state").textContent = "재생 준비됨";
}

async function loadItem(itemHash) {
  try {
    element("save-review").disabled = true;
    const item = await requestJson(
      `${API_PREFIX}/review-items/${encodeURIComponent(itemHash)}`,
    );
    state.currentItemHash = itemHash;
    state.currentItem = item;
    renderItemList();

    element("empty-state").hidden = true;
    element("review-workspace").hidden = false;
    element("item-kicker").textContent = `ITEM ${item.audit_index}`;
    element("item-title").textContent = state.profile === T43_PROFILE
      ? "원인 adjudication"
      : "phone-level 독립 감사";
    element("item-hash").textContent = item.item_sha256;
    element("item-status").textContent = item.reviewed ? "검수 완료" : "미완료";
    element("item-status").classList.toggle("is-complete", item.reviewed);

    const chips = element("metadata-chips");
    chips.replaceChildren();
    appendMetadataChip("길이", `${item.duration_seconds.toFixed(2)}s`);
    appendMetadataChip("L1", item.l1);
    appendMetadataChip("성별", item.gender);
    appendMetadataChip("숙련도", item.proficiency);
    appendMetadataChip("error", item.error_presence ? "있음" : "없음");

    renderAlignmentTable(item.automatic_phone_alignments);
    renderIntervals("target-intervals", item.target_phone_intervals);
    renderIntervals("realized-intervals", item.realized_phone_intervals);
    resetForm(item.review, item.automatic_phone_alignments.length);
    await loadAudio(itemHash);
    element("save-review").disabled = false;
  } catch (error) {
    setFormMessage(error.message, true);
    element("audio-state").textContent = "오디오 오류";
  }
}

function validateFormPayload(payload) {
  if (payload.alignment_correct && payload.incorrect_interval_count !== 0) {
    throw new Error("정렬이 정확하면 잘못 대응된 interval 수는 0이어야 합니다.");
  }
  if (!payload.alignment_correct && payload.incorrect_interval_count === 0) {
    throw new Error("정렬이 부정확하면 잘못 대응된 interval 수가 1 이상이어야 합니다.");
  }
}

function buildSubmission() {
  const isT43 = state.profile === T43_PROFILE;
  const payload = {
    alignment_correct: element("alignment-correct").value === "true",
    maximum_boundary_error_ms: Number(element("boundary-error").value),
    incorrect_interval_count: Number(element("incorrect-count").value),
    annotation_fidelity: isT43 ? element("annotation-fidelity").value : null,
    root_cause: isT43 ? element("root-cause").value : null,
    resolution: isT43 ? element("resolution").value : null,
    notes: element("review-notes").value,
  };
  validateFormPayload(payload);
  return payload;
}

async function refreshSessionAndItems() {
  const [session, items] = await Promise.all([
    requestJson(`${API_PREFIX}/review-session`),
    requestJson(`${API_PREFIX}/review-items`),
  ]);
  updateSession(session);
  state.items = items;
  renderItemList();
}

async function saveReview(event) {
  event.preventDefault();
  if (!state.currentItem) {
    return;
  }
  try {
    setFormMessage("");
    element("save-review").disabled = true;
    const payload = buildSubmission();
    const method = state.currentItem.reviewed ? "PUT" : "POST";
    await requestJson(
      `${API_PREFIX}/review-items/${encodeURIComponent(state.currentItemHash)}/review`,
      {method, body: JSON.stringify(payload)},
    );
    state.currentItem.reviewed = true;
    state.currentItem.review = payload;
    await refreshSessionAndItems();
    element("item-status").textContent = "검수 완료";
    element("item-status").classList.add("is-complete");
    setFormMessage("판정이 source hash에 결박된 state에 원자 저장되었습니다.");
  } catch (error) {
    setFormMessage(error.message, true);
  } finally {
    element("save-review").disabled = false;
  }
}

function selectNextPending() {
  if (state.items.length === 0) {
    return;
  }
  const currentIndex = state.items.findIndex(
    (item) => item.item_sha256 === state.currentItemHash,
  );
  const orderedItems = state.items.slice(currentIndex + 1).concat(state.items.slice(0, currentIndex + 1));
  const next = orderedItems.find((item) => !item.reviewed);
  if (next) {
    loadItem(next.item_sha256);
  } else {
    setFormMessage("모든 항목의 판정이 저장되었습니다.");
  }
}

function bindEvents() {
  element("review-form").addEventListener("submit", saveReview);
  element("next-pending").addEventListener("click", selectNextPending);
  element("item-search").addEventListener("input", renderItemList);
  element("review-notes").addEventListener("input", () => {
    element("note-count").textContent = element("review-notes").value.length;
  });
  element("root-cause").addEventListener("change", (event) => {
    updateResolutionOptions(event.target.value);
  });
  element("alignment-correct").addEventListener("change", (event) => {
    if (event.target.value === "true") {
      element("incorrect-count").value = 0;
    } else if (event.target.value === "false" && element("incorrect-count").value === "0") {
      element("incorrect-count").value = "";
    }
  });
  for (const button of document.querySelectorAll(".filter-button")) {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      for (const sibling of document.querySelectorAll(".filter-button")) {
        sibling.classList.toggle("is-active", sibling === button);
      }
      renderItemList();
    });
  }
}

async function bootstrap() {
  bindEvents();
  state.token = resolveAccessToken();
  if (!state.token) {
    setFatalMessage("접근 token이 없습니다. 서버가 출력한 #token=... 포함 URL을 다시 여세요.");
    return;
  }
  try {
    await refreshSessionAndItems();
  } catch (error) {
    setFatalMessage(error.message);
  }
}

window.addEventListener("beforeunload", () => {
  if (state.audioObjectUrl) {
    URL.revokeObjectURL(state.audioObjectUrl);
  }
});

bootstrap();
