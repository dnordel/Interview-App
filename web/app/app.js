import {
  applyApplicationData,
  applyDraftPayload,
  buildWebDraftPayload,
  createCustomResponse,
  createInitialState,
  createTraitResponse,
  customById as customByIdData,
  customQuestionsForTrack as customQuestionsForTrackData,
  finalizeWebDraftToBackend,
  flowForTrack as flowForTrackData,
  loadApplicationData,
  loadDraftsFromBackend,
  loadSignalDefinition,
  loadWebDraftFromBackend,
  primaryQuestion as primaryQuestionData,
  saveQuestionOverridesToBackend,
  saveOfferSettingsToBackend,
  saveWebDraftToBackend,
  scoreWebDraftPreview,
  tracksFromRubric,
  traitById as traitByIdData,
  updateHistoryOfferStatusToBackend,
  uploadWebRecordingToBackend,
} from "./data.js";

const app = document.querySelector("#app");
const state = createInitialState();
const activeRecording = {
  recorder: null,
  stream: null,
  chunks: [],
  flowIndex: null,
  itemId: "",
  mimeType: "",
};

async function loadData() {
  applyApplicationData(state, await loadApplicationData());
  const drafts = await loadDraftsFromBackend();
  state.drafts = drafts.ok ? drafts.drafts : [];
  await loadSignalsForCurrentFlow();
}

function routeTo(route) {
  state.route = route;
  state.message = "";
  state.messageType = "";
  render();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function tracks() {
  return tracksFromRubric(state.rubric);
}

function customQuestionsForTrack(track) {
  return customQuestionsForTrackData(state.overrides, track);
}

function flowForTrack(track) {
  return flowForTrackData(state.rubric, state.overrides, track);
}

function currentFlow() {
  return flowForTrack(state.candidate.track);
}

function currentItem() {
  return currentFlow()[state.flowIndex] || currentFlow()[0];
}

function traitById(traitId) {
  return traitByIdData(state.rubric, traitId);
}

function customById(customId) {
  return customByIdData(state.overrides, state.candidate.track, customId);
}

function primaryQuestion(trait) {
  return primaryQuestionData(state.overrides, trait);
}

function traitResponse(traitId) {
  if (!state.traitInputs[traitId]) {
    state.traitInputs[traitId] = createTraitResponse();
  }
  return state.traitInputs[traitId];
}

function customResponse(customId) {
  if (!state.customInputs[customId]) {
    state.customInputs[customId] = createCustomResponse();
  }
  return state.customInputs[customId];
}

async function loadSignalsForCurrentFlow() {
  state.signalDefinition = await loadSignalDefinition(currentItem());
}

function scoreStatus(response) {
  if (response.absolute_disqualifier) {
    return "Score disabled because absolute disqualifier is checked.";
  }
  if (response.raw_score) {
    return `Selected score: ${response.raw_score}`;
  }
  return "Select 1-5 before continuing.";
}

function anchorsHtml(title, values) {
  const rows = [5, 4, 3, 2, 1]
    .map((score) => {
      const copy = values?.[score] ?? values?.[String(score)] ?? "";
      return `
        <div class="anchor-item">
          <span class="anchor-num">${score}</span>
          <span>${escapeHtml(copy)}</span>
        </div>
      `;
    })
    .join("");
  return `
    <details class="accordion">
      <summary>${escapeHtml(title)}</summary>
      <div class="anchor-list">${rows}</div>
    </details>
  `;
}

function signalChecklistHtml(response) {
  const signals = [
    ...(state.signalDefinition?.core_signals || []),
    ...(state.signalDefinition?.extended_signals || []),
  ];
  if (!signals.length) {
    return `<p class="helper">No signal checklist loaded for this competency.</p>`;
  }
  return `
    <div class="checklist">
      ${signals
        .map((signal) => {
          const id = signal.id || signal.signal_id;
          return `
            <label class="check-row">
              <input type="checkbox" data-signal="${escapeHtml(id)}" ${
                response.selected_signal_ids.includes(id) ? "checked" : ""
              } />
              <span>${escapeHtml(signal.label || id)}</span>
            </label>
          `;
        })
        .join("")}
    </div>
  `;
}

function shell(title, body) {
  const nav = [
    ["start", "Start"],
    ["candidate", "Candidate Setup"],
    ["interview", "Interview"],
    ["review", "Review"],
    ["onboarding", "Onboarding"],
    ["settings", "Settings"],
    ["questions", "Question Editor"],
    ["history", "History"],
  ];
  app.innerHTML = `
    <div class="layout">
      <aside class="sidebar">
        <h1 class="brand">Preschool Interview Web</h1>
        <nav class="nav" aria-label="Web app screens">
          ${nav
            .map(
              ([route, label]) => `
                <button class="nav-button" data-route="${route}" aria-current="${
                  state.route === route ? "page" : "false"
                }">${label}</button>
              `,
            )
            .join("")}
        </nav>
        <div class="shell-note">
          Tk desktop app remains main entry point. Web screens are migration work in parallel.
        </div>
      </aside>
      <main class="content">
        <section class="topbar">
          <h1>${escapeHtml(title)}</h1>
          <div class="action-row">
            <button id="saveWebDraft" class="button" type="button">Save draft</button>
            <button id="exportWebDraft" class="button" type="button">Export web draft</button>
          </div>
        </section>
        ${body}
      </main>
    </div>
  `;
  document.querySelectorAll("[data-route]").forEach((button) => {
    button.addEventListener("click", () => routeTo(button.dataset.route));
  });
  document.querySelector("#saveWebDraft")?.addEventListener("click", saveWebDraft);
  document.querySelector("#exportWebDraft")?.addEventListener("click", exportWebDraft);
}

function messageHtml() {
  if (!state.message) {
    return "";
  }
  return `<div class="message ${state.messageType}">${escapeHtml(state.message)}</div>`;
}

function renderStart() {
  shell(
    "Start",
    `
      <section class="hero">
        <h2>Run interviews in Tk today. Build web parity here.</h2>
        <p class="helper">
          This web app starts full migration without replacing the desktop entry point. Use it to exercise start,
          setup, interview, onboarding, settings, question editing, and history screens against local JSON data.
        </p>
        <div class="action-row">
          <button class="button primary" data-start-action="new">New interview</button>
          <button class="button" data-start-action="questions">Edit questions</button>
          <button class="button" data-start-action="settings">Settings</button>
        </div>
      </section>
      <section class="grid two">
        <div class="panel">
          <h3>Migration coverage</h3>
          <div class="list">
            ${["Start", "Candidate setup", "Interview flow", "Onboarding", "Settings", "Question editor", "History"]
              .map((label, index) => `<div class="anchor-item"><span class="anchor-num">${index + 1}</span><span>${label}</span></div>`)
              .join("")}
          </div>
        </div>
        <div class="panel">
          <h3>Desktop entry point</h3>
          <p class="helper">No launch scripts are changed by this web migration slice.</p>
          <p><span class="pill green">Tk remains primary</span></p>
        </div>
      </section>
      <section class="panel">
        <h3>Saved web drafts</h3>
        ${
          state.drafts.length
            ? `<div class="list">${state.drafts
                .map(
                  (draft) => `
                    <div class="list-item draft-row">
                      <span>
                        <strong>${escapeHtml(draft.candidate_name || "Unknown candidate")}</strong>
                        <span class="muted">${escapeHtml([draft.interview_date, draft.school, draft.track].filter(Boolean).join(" | "))}</span>
                      </span>
                      <button class="button row-action" data-draft-name="${escapeHtml(draft.draft_name)}" type="button">Resume</button>
                    </div>
                  `,
                )
                .join("")}</div>`
            : `<p class="helper">No backend drafts available. Static file-server mode can still export drafts.</p>`
        }
      </section>
    `,
  );
  document.querySelector("[data-start-action='new']")?.addEventListener("click", () => routeTo("candidate"));
  document.querySelector("[data-start-action='questions']")?.addEventListener("click", () => routeTo("questions"));
  document.querySelector("[data-start-action='settings']")?.addEventListener("click", () => routeTo("settings"));
  document.querySelectorAll("[data-draft-name]").forEach((button) => {
    button.addEventListener("click", () => resumeDraft(button.dataset.draftName));
  });
}

function renderCandidate() {
  shell(
    "Candidate Setup",
    `
      <section class="panel">
        <h3>Candidate information</h3>
        <div class="grid two">
          <div class="field">
            <label for="candidateName">Candidate name</label>
            <input id="candidateName" class="input" value="${escapeHtml(state.candidate.candidate_name)}" />
          </div>
          <div class="field">
            <label for="interviewDate">Interview date</label>
            <input id="interviewDate" class="input" type="date" value="${escapeHtml(state.candidate.interview_date)}" />
          </div>
          <div class="field">
            <label for="school">School/location</label>
            <input id="school" class="input" value="${escapeHtml(state.candidate.school)}" />
          </div>
          <div class="field">
            <label for="track">Track</label>
            <select id="track" class="select">
              ${tracks()
                .map(
                  (track) => `<option value="${track.key}" ${track.key === state.candidate.track ? "selected" : ""}>${escapeHtml(track.label)}</option>`,
                )
                .join("")}
            </select>
          </div>
        </div>
        ${messageHtml()}
        <div class="action-row">
          <button id="startInterview" class="button primary" type="button">Start interview</button>
        </div>
      </section>
    `,
  );
  bindCandidateFields();
  document.querySelector("#startInterview")?.addEventListener("click", async () => {
    if (!state.candidate.candidate_name.trim()) {
      state.message = "Candidate name is required before starting.";
      state.messageType = "error";
      renderCandidate();
      return;
    }
    state.flowIndex = 0;
    await loadSignalsForCurrentFlow();
    routeTo("interview");
  });
}

function bindCandidateFields() {
  document.querySelector("#candidateName")?.addEventListener("input", (event) => {
    state.candidate.candidate_name = event.target.value;
  });
  document.querySelector("#interviewDate")?.addEventListener("input", (event) => {
    state.candidate.interview_date = event.target.value;
  });
  document.querySelector("#school")?.addEventListener("input", (event) => {
    state.candidate.school = event.target.value;
  });
  document.querySelector("#track")?.addEventListener("change", async (event) => {
    state.candidate.track = event.target.value;
    state.questionEditor.selectedTrack = event.target.value;
    state.flowIndex = 0;
    await loadSignalsForCurrentFlow();
    renderCandidate();
  });
}

function renderInterview() {
  const flow = currentFlow();
  const item = currentItem();
  if (!item) {
    shell("Interview", `<section class="panel"><h3>No questions configured</h3></section>`);
    return;
  }
  if (item.type === "custom") {
    renderCustomQuestion(flow, item);
    return;
  }
  renderTraitQuestion(flow, item);
}

function renderCustomQuestion(flow, item) {
  const custom = customById(item.id) || { id: item.id, text: item.id };
  const response = customResponse(custom.id);
  shell(
    "Interview",
    `
      ${interviewHeaderHtml(flow, "Custom question", custom.text)}
      ${audioPanelHtml(item)}
      <section class="panel">
        <h3>Candidate answer</h3>
        <textarea id="customAnswer" class="textarea">${escapeHtml(response.answer)}</textarea>
      </section>
      ${messageHtml()}
      ${interviewFooterHtml(flow)}
    `,
  );
  document.querySelector("#customAnswer")?.addEventListener("input", (event) => {
    response.answer = event.target.value;
  });
  bindAudioControls(item);
  bindInterviewFooter();
}

function renderTraitQuestion(flow, item) {
  const trait = traitById(item.id) || state.rubric.traits[0];
  const response = traitResponse(trait.id);
  shell(
    "Interview",
    `
      ${interviewHeaderHtml(flow, "Scored competency", primaryQuestion(trait), trait)}
      ${audioPanelHtml(item)}
      <section class="grid workspace">
        <div>
          <section class="panel">
            <h3>Candidate notes</h3>
            <div class="field">
              <label for="questionNotes">Question notes</label>
              <textarea id="questionNotes" class="textarea">${escapeHtml(response.question_notes)}</textarea>
            </div>
            <div class="field">
              <label for="traitNotes">Scored competency notes</label>
              <textarea id="traitNotes" class="textarea short">${escapeHtml(response.trait_notes)}</textarea>
            </div>
          </section>
          <section class="panel">
            <h3>Trait observations</h3>
            ${signalChecklistHtml(response)}
          </section>
        </div>
        <aside>
          <section class="panel decision-panel">
            <h3>Raw score</h3>
            <div class="score-grid">
              ${[1, 2, 3, 4, 5]
                .map(
                  (score) => `<button class="score-button" data-score="${score}" aria-pressed="${
                    response.raw_score === score
                  }" ${response.absolute_disqualifier ? "disabled" : ""}>${score}</button>`,
                )
                .join("")}
            </div>
            <div class="status-line">${escapeHtml(scoreStatus(response))}</div>
          </section>
          <section class="panel danger-panel">
            <label><input id="dqToggle" type="checkbox" ${response.absolute_disqualifier ? "checked" : ""} /> Absolute disqualifier observed</label>
            <div class="field" ${response.absolute_disqualifier ? "" : "hidden"}>
              <label for="verbatimNotes">Verbatim quote/notes required</label>
              <textarea id="verbatimNotes" class="textarea short">${escapeHtml(response.verbatim_notes)}</textarea>
            </div>
          </section>
          <section class="panel">
            <h3>No usable example</h3>
            <label><input id="noExample" type="checkbox" ${response.no_example_after_followups ? "checked" : ""} /> Cap score at 3</label>
          </section>
          <section class="panel">
            <h3>Reference</h3>
            ${anchorsHtml("Scoring descriptors", trait.descriptors)}
            ${anchorsHtml("Sample answers", trait.sample_answers)}
            ${anchorsHtml("Global disqualifiers", Object.fromEntries((state.rubric.absolute_disqualifiers || []).slice(0, 5).map((item, index) => [5 - index, item])))}
          </section>
        </aside>
      </section>
      ${messageHtml()}
      ${interviewFooterHtml(flow)}
    `,
  );
  bindTraitQuestion(trait.id);
  bindAudioControls(item);
  bindInterviewFooter();
}

function interviewHeaderHtml(flow, kind, question, trait = null) {
  const progress = Math.round(((state.flowIndex + 1) / Math.max(flow.length, 1)) * 100);
  return `
    <section class="hero">
      <div class="action-row">
        <span class="pill">${kind}</span>
        <span class="pill green">Question ${state.flowIndex + 1} of ${flow.length}</span>
        ${trait ? `<span class="pill ${trait.priority === "Critical" ? "critical" : ""}">${escapeHtml(trait.priority)}</span>` : ""}
        ${trait ? `<span class="pill green">Weight x${escapeHtml(trait.weight)}</span>` : ""}
      </div>
      <h2>${trait ? escapeHtml(trait.name) : "Custom question"}</h2>
      <p class="helper"><strong>Question:</strong> ${escapeHtml(question)}</p>
      <div class="progress"><span style="width:${progress}%"></span></div>
    </section>
  `;
}

function interviewFooterHtml(flow) {
  return `
    <section class="footer-actions">
      <div class="action-row">
        <button id="backQuestion" class="button" type="button" ${state.flowIndex === 0 ? "disabled" : ""}>Back</button>
        <button id="skipQuestion" class="button" type="button">Skip</button>
      </div>
      <div class="action-row">
        <button id="nextQuestion" class="button primary" type="button">${state.flowIndex === flow.length - 1 ? "Review complete" : "Continue"}</button>
      </div>
    </section>
  `;
}

function audioPanelHtml(item) {
  const active = activeRecording.recorder && activeRecording.flowIndex === state.flowIndex;
  const saved = recordingForFlow(state.flowIndex);
  const unsupported = !("mediaDevices" in navigator) || !("MediaRecorder" in window);
  return `
    <section class="panel audio-panel">
      <div>
        <h3>Audio</h3>
        <p class="helper">${escapeHtml(audioStatusText(active, saved, unsupported))}</p>
      </div>
      <div class="action-row">
        <button id="startRecording" class="button" type="button" ${active || unsupported ? "disabled" : ""}>Start recording</button>
        <button id="stopRecording" class="button primary" type="button" ${active ? "" : "disabled"}>Stop and save</button>
      </div>
    </section>
  `;
}

function audioStatusText(active, saved, unsupported) {
  if (unsupported) {
    return "Browser audio recording unavailable.";
  }
  if (active) {
    return "Recording this question.";
  }
  if (saved) {
    return `Saved ${Math.round(Number(saved.byte_count || 0) / 1024)} KB for this question.`;
  }
  return "No recording saved for this question.";
}

function recordingForFlow(flowIndex) {
  return (state.flowRecordings || []).find((item) => Number(item.flow_index) === Number(flowIndex));
}

function bindTraitQuestion(traitId) {
  const response = traitResponse(traitId);
  document.querySelector("#questionNotes")?.addEventListener("input", (event) => {
    response.question_notes = event.target.value;
  });
  document.querySelector("#traitNotes")?.addEventListener("input", (event) => {
    response.trait_notes = event.target.value;
  });
  document.querySelector("#verbatimNotes")?.addEventListener("input", (event) => {
    response.verbatim_notes = event.target.value;
  });
  document.querySelectorAll("[data-score]").forEach((button) => {
    button.addEventListener("click", () => {
      if (response.absolute_disqualifier) {
        return;
      }
      response.raw_score = Number(button.dataset.score);
      if (response.no_example_after_followups && response.raw_score > 3) {
        response.raw_score = 3;
        state.message = "No usable example after follow-ups caps this score at 3.";
        state.messageType = "error";
      } else {
        state.message = "";
        state.messageType = "";
      }
      renderInterview();
    });
  });
  document.querySelector("#dqToggle")?.addEventListener("change", (event) => {
    response.absolute_disqualifier = event.target.checked;
    if (response.absolute_disqualifier) {
      response.raw_score = null;
    }
    renderInterview();
  });
  document.querySelector("#noExample")?.addEventListener("change", (event) => {
    response.no_example_after_followups = event.target.checked;
    if (response.no_example_after_followups && response.raw_score > 3) {
      response.raw_score = 3;
    }
    renderInterview();
  });
  document.querySelectorAll("[data-signal]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const signalId = checkbox.dataset.signal;
      const selected = new Set(response.selected_signal_ids);
      if (checkbox.checked) {
        selected.add(signalId);
      } else {
        selected.delete(signalId);
      }
      response.selected_signal_ids = [...selected];
    });
  });
}

function bindAudioControls(item) {
  document.querySelector("#startRecording")?.addEventListener("click", () => startBrowserRecording(item));
  document.querySelector("#stopRecording")?.addEventListener("click", stopBrowserRecording);
}

async function startBrowserRecording(item) {
  if (activeRecording.recorder) {
    state.message = "Stop the current recording before starting another.";
    state.messageType = "error";
    renderInterview();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = preferredAudioMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    activeRecording.recorder = recorder;
    activeRecording.stream = stream;
    activeRecording.chunks = [];
    activeRecording.flowIndex = state.flowIndex;
    activeRecording.itemId = item?.id || `question_${state.flowIndex + 1}`;
    activeRecording.mimeType = recorder.mimeType || mimeType || "audio/webm";
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size) {
        activeRecording.chunks.push(event.data);
      }
    });
    recorder.start();
    state.message = "";
    state.messageType = "";
    renderInterview();
  } catch (_error) {
    resetActiveRecording();
    state.message = "Browser recording could not start.";
    state.messageType = "error";
    renderInterview();
  }
}

function preferredAudioMimeType() {
  for (const mimeType of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]) {
    if (MediaRecorder.isTypeSupported?.(mimeType)) {
      return mimeType;
    }
  }
  return "";
}

async function stopBrowserRecording() {
  const recorder = activeRecording.recorder;
  if (!recorder) {
    return;
  }
  const stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve, { once: true }));
  recorder.stop();
  await stopped;
  activeRecording.stream?.getTracks().forEach((track) => track.stop());
  const blob = new Blob(activeRecording.chunks, { type: activeRecording.mimeType || "audio/webm" });
  const result = await uploadWebRecordingToBackend({
    flow_index: activeRecording.flowIndex,
    question_id: activeRecording.itemId,
    mime_type: blob.type || activeRecording.mimeType || "audio/webm",
    data_base64: await blobToBase64(blob),
  });
  if (result.ok) {
    state.flowRecordings = [
      ...(state.flowRecordings || []).filter((item) => Number(item.flow_index) !== Number(result.payload.flow_index)),
      result.payload,
    ];
    state.message = "Recording saved.";
    state.messageType = "ok";
  } else {
    state.message = `${result.error} Recording was not saved.`;
    state.messageType = "error";
  }
  resetActiveRecording();
  renderInterview();
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",", 2)[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function resetActiveRecording() {
  activeRecording.stream?.getTracks().forEach((track) => track.stop());
  activeRecording.recorder = null;
  activeRecording.stream = null;
  activeRecording.chunks = [];
  activeRecording.flowIndex = null;
  activeRecording.itemId = "";
  activeRecording.mimeType = "";
}

function hasActiveRecordingForCurrentQuestion() {
  return activeRecording.recorder && activeRecording.flowIndex === state.flowIndex;
}

function validateCurrentQuestion() {
  const item = currentItem();
  if (!item || item.type !== "trait") {
    return true;
  }
  const response = traitResponse(item.id);
  if (!response.absolute_disqualifier && !response.raw_score) {
    state.message = "A raw score is missing. Select 1-5, or check absolute disqualifier.";
    state.messageType = "error";
    renderInterview();
    return false;
  }
  if (response.absolute_disqualifier && !response.verbatim_notes.trim()) {
    state.message = "Absolute disqualifier needs supporting quote or notes.";
    state.messageType = "error";
    renderInterview();
    return false;
  }
  return true;
}

function bindInterviewFooter() {
  document.querySelector("#backQuestion")?.addEventListener("click", async () => {
    if (hasActiveRecordingForCurrentQuestion()) {
      state.message = "Stop and save recording before leaving this question.";
      state.messageType = "error";
      renderInterview();
      return;
    }
    state.flowIndex = Math.max(0, state.flowIndex - 1);
    state.message = "";
    await loadSignalsForCurrentFlow();
    renderInterview();
  });
  document.querySelector("#skipQuestion")?.addEventListener("click", async () => {
    if (hasActiveRecordingForCurrentQuestion()) {
      state.message = "Stop and save recording before leaving this question.";
      state.messageType = "error";
      renderInterview();
      return;
    }
    const item = currentItem();
    if (item?.type === "trait") {
      traitResponse(item.id).skipped = true;
    }
    state.flowIndex = Math.min(currentFlow().length - 1, state.flowIndex + 1);
    await loadSignalsForCurrentFlow();
    renderInterview();
  });
  document.querySelector("#nextQuestion")?.addEventListener("click", async () => {
    if (hasActiveRecordingForCurrentQuestion()) {
      state.message = "Stop and save recording before leaving this question.";
      state.messageType = "error";
      renderInterview();
      return;
    }
    if (!validateCurrentQuestion()) {
      return;
    }
    if (state.flowIndex >= currentFlow().length - 1) {
      await showReview();
      return;
    }
    state.flowIndex = Math.min(currentFlow().length - 1, state.flowIndex + 1);
    state.message = "";
    await loadSignalsForCurrentFlow();
    renderInterview();
  });
}

async function showReview() {
  state.message = "";
  state.messageType = "";
  state.scorePreview = null;
  state.finalizeResult = null;
  const result = await scoreWebDraftPreview(buildWebDraftPayload(state));
  if (result.ok) {
    state.scorePreview = result.payload.scorePreview;
  } else {
    state.message = `${result.error} Save or export the draft, then finalize from Tk.`;
    state.messageType = "error";
  }
  state.route = "review";
  render();
}

function renderReview() {
  const preview = state.scorePreview;
  const rows = Array.isArray(preview?.rows) ? preview.rows : [];
  shell(
    "Review",
    `
      <section class="hero">
        <div class="action-row">
          <span class="pill green">${escapeHtml(state.candidate.candidate_name || "Candidate")}</span>
          <span class="pill">${escapeHtml(state.candidate.track || "Track")}</span>
          ${preview ? `<span class="pill ${preview.outcome === "No Hire" ? "critical" : "green"}">${escapeHtml(preview.outcome)}</span>` : ""}
        </div>
        <h2>Score preview</h2>
        <p class="helper">Read-only scoring preview. Report generation and final workflow remain in Tk during migration.</p>
      </section>
      ${messageHtml()}
      ${
        preview
          ? `
            <section class="grid three">
              <div class="panel metric"><span class="metric-value">${escapeHtml(preview.percent_of_max_label)}</span><span class="helper">Percent of max</span></div>
              <div class="panel metric"><span class="metric-value">${escapeHtml(preview.weighted_total)}</span><span class="helper">Weighted total</span></div>
              <div class="panel metric"><span class="metric-value">${escapeHtml(preview.scored_traits_count)}</span><span class="helper">Scored competencies</span></div>
            </section>
            ${preview.locked_rule ? `<section class="panel danger-panel"><strong>Locked rule:</strong> ${escapeHtml(preview.locked_rule)}</section>` : ""}
            <section class="panel">
              <h3>Competency results</h3>
              <table class="table">
                <thead><tr><th>Competency</th><th>Priority</th><th>Raw</th><th>Weight</th><th>Weighted</th><th>Status</th></tr></thead>
                <tbody>
                  ${rows
                    .map(
                      (row) => `
                        <tr>
                          <td>${escapeHtml(row.trait_name)}</td>
                          <td>${escapeHtml(row.priority)}</td>
                          <td>${escapeHtml(row.raw_score ?? "")}</td>
                          <td>${escapeHtml(row.weight)}</td>
                          <td>${escapeHtml(row.weighted_score)}</td>
                          <td>${escapeHtml(reviewRowStatus(row))}</td>
                        </tr>
                      `,
                    )
                    .join("")}
                </tbody>
              </table>
            </section>
            ${
              state.finalizeResult
                ? `<section class="panel">
                    <h3>Finalized outputs</h3>
                    <p class="helper"><strong>Report:</strong> ${escapeHtml(state.finalizeResult.report_path || "")}</p>
                    <p class="helper"><strong>Integration export:</strong> ${escapeHtml(state.finalizeResult.integration_path || "")}</p>
                    <p class="helper"><strong>Director referral packet:</strong> ${escapeHtml(referralPacketStatus(state.finalizeResult.director_packet))}</p>
                  </section>`
                : ""
            }
          `
          : `<section class="panel"><h3>Preview unavailable</h3><p class="helper">Static mode cannot score. Use Save draft when the local backend is running, or export the draft for Tk.</p></section>`
      }
      <section class="footer-actions">
        <div class="action-row">
          <button id="backToInterview" class="button" type="button">Back to interview</button>
        </div>
        <div class="action-row">
          <button id="reviewSaveDraft" class="button" type="button">Save draft</button>
          <button id="reviewExportDraft" class="button" type="button">Export web draft</button>
          <button id="reviewFinalize" class="button primary" type="button">Finalize report</button>
        </div>
      </section>
    `,
  );
  document.querySelector("#backToInterview")?.addEventListener("click", () => routeTo("interview"));
  document.querySelector("#reviewSaveDraft")?.addEventListener("click", saveWebDraft);
  document.querySelector("#reviewExportDraft")?.addEventListener("click", exportWebDraft);
  document.querySelector("#reviewFinalize")?.addEventListener("click", finalizeWebDraft);
}

function reviewRowStatus(row) {
  if (row.absolute_disqualifier) {
    return "Disqualifier";
  }
  if (row.skipped) {
    return "Skipped";
  }
  if (row.no_example_after_followups) {
    return "No example cap";
  }
  return "Scored";
}

function referralPacketStatus(packet) {
  if (!packet || typeof packet !== "object") {
    return "Not built";
  }
  const candidateName = packet.candidate?.name || "candidate";
  const outcome = packet.scoring?.outcome || "outcome pending";
  return `Built for ${candidateName} (${outcome}); not sent from web.`;
}

function renderOnboarding() {
  const rows = Object.entries(state.offerSettings || {});
  const historyRows = (Array.isArray(state.history) ? state.history : []).slice(0, 12);
  shell(
    "Onboarding",
    `
      <section class="hero">
        <h2>Onboarding workspace</h2>
        <p class="helper">Read-only web migration shell for offer/onboarding settings. Reminder sending remains desktop-only for now.</p>
      </section>
      <section class="panel">
        <h3>Offer settings by school</h3>
        ${rows.length ? settingsTable(rows) : `<p class="helper">No school offer settings loaded.</p>`}
      </section>
      <section class="grid two">
        <div class="panel"><h3>Daily workflow</h3><p class="helper">Run reminders, dry runs, and task updates will be wired after backend API exists.</p></div>
        <div class="panel"><h3>Candidate management</h3><p class="helper">Offer status updates are available for finalized interview rows when the backend is running.</p></div>
      </section>
      <section class="panel">
        <h3>Recent offer statuses</h3>
        ${historyStatusTable(historyRows)}
      </section>
      ${messageHtml()}
    `,
  );
  bindHistoryStatusControls();
}

function settingsTable(rows) {
  return `
    <table class="table">
      <thead><tr><th>School</th><th>Full time template</th><th>Part time template</th><th>Offer output</th><th>Interview notes</th></tr></thead>
      <tbody>
        ${rows
          .map(
            ([school, value]) => `
              <tr>
                <td>${escapeHtml(school)}</td>
                <td>${escapeHtml(value.full_time_template || "")}</td>
                <td>${escapeHtml(value.part_time_template || "")}</td>
                <td>${escapeHtml(value.offer_output_dir || "")}</td>
                <td>${escapeHtml(value.interview_notes_dir || "")}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function renderSettings() {
  shell(
    "Settings",
    `
      <section class="grid two">
        <div class="panel">
          <h3>General</h3>
          <div class="field"><label>Default track</label><input class="input" value="${escapeHtml(state.candidate.track)}" readonly /></div>
          <div class="field"><label>Base mode</label><input class="input" value="Web migration preview" readonly /></div>
        </div>
        <div class="panel">
          <h3>Security</h3>
          <p class="helper">Candidate/interview data is held in memory only unless explicitly exported. Tk remains production entry point.</p>
          <span class="pill green">No browser storage</span>
        </div>
      </section>
      <section class="panel">
        <h3>Offer settings</h3>
        ${editableOfferSettingsHtml(Object.entries(state.offerSettings || {}))}
        ${messageHtml()}
        <div class="action-row">
          <button id="addOfferSchool" class="button" type="button">Add school</button>
          <button id="saveOfferSettings" class="button primary" type="button">Save offer settings</button>
        </div>
      </section>
    `,
  );
  bindOfferSettingsControls();
}

function editableOfferSettingsHtml(rows) {
  const effectiveRows = rows.length
    ? rows
    : [["", { full_time_template: "", part_time_template: "", offer_output_dir: "", interview_notes_dir: "" }]];
  return `
    <div class="settings-editor">
      ${effectiveRows
        .map(
          ([school, value], index) => `
            <section class="settings-row" data-offer-row="${index}">
              <div class="field">
                <label>School</label>
                <input class="input" data-offer-field="school" value="${escapeHtml(school)}" />
              </div>
              <div class="field">
                <label>Full-time template</label>
                <input class="input" data-offer-field="full_time_template" value="${escapeHtml(value.full_time_template || "")}" />
              </div>
              <div class="field">
                <label>Part-time template</label>
                <input class="input" data-offer-field="part_time_template" value="${escapeHtml(value.part_time_template || "")}" />
              </div>
              <div class="field">
                <label>Offer output folder</label>
                <input class="input" data-offer-field="offer_output_dir" value="${escapeHtml(value.offer_output_dir || "")}" />
              </div>
              <div class="field">
                <label>Interview notes folder</label>
                <input class="input" data-offer-field="interview_notes_dir" value="${escapeHtml(value.interview_notes_dir || "")}" />
              </div>
            </section>
          `,
        )
        .join("")}
    </div>
  `;
}

function bindOfferSettingsControls() {
  document.querySelectorAll("[data-offer-field]").forEach((input) => {
    input.addEventListener("input", updateOfferSettingsFromEditor);
  });
  document.querySelector("#addOfferSchool")?.addEventListener("click", () => {
    let next = "New School";
    let index = 2;
    while (state.offerSettings[next]) {
      next = `New School ${index}`;
      index += 1;
    }
    state.offerSettings[next] = { full_time_template: "", part_time_template: "", offer_output_dir: "", interview_notes_dir: "" };
    renderSettings();
  });
  document.querySelector("#saveOfferSettings")?.addEventListener("click", saveOfferSettings);
}

function updateOfferSettingsFromEditor() {
  const updated = {};
  document.querySelectorAll("[data-offer-row]").forEach((row) => {
    const school = row.querySelector("[data-offer-field='school']")?.value.trim();
    if (!school) {
      return;
    }
    updated[school] = {
      full_time_template: row.querySelector("[data-offer-field='full_time_template']")?.value.trim() || "",
      part_time_template: row.querySelector("[data-offer-field='part_time_template']")?.value.trim() || "",
      offer_output_dir: row.querySelector("[data-offer-field='offer_output_dir']")?.value.trim() || "",
      interview_notes_dir: row.querySelector("[data-offer-field='interview_notes_dir']")?.value.trim() || "",
    };
  });
  state.offerSettings = updated;
}

function renderQuestions() {
  const selectedTrait = traitById(state.questionEditor.selectedTraitId) || state.rubric.traits[0];
  const custom = customQuestionsForTrack(state.questionEditor.selectedTrack);
  shell(
    "Question Editor",
    `
      <section class="grid two">
        <div class="panel">
          <h3>Scored competencies</h3>
          <div class="field">
            <label for="traitSelect">Trait</label>
            <select id="traitSelect" class="select">
              ${(state.rubric.traits || [])
                .map((trait) => `<option value="${trait.id}" ${trait.id === selectedTrait.id ? "selected" : ""}>${escapeHtml(trait.name)}</option>`)
                .join("")}
            </select>
          </div>
          <div class="field"><label>Name</label><input class="input" value="${escapeHtml(selectedTrait.name)}" readonly /></div>
          <div class="field"><label for="traitQuestionText">Primary question</label><textarea id="traitQuestionText" class="textarea short">${escapeHtml(primaryQuestion(selectedTrait))}</textarea></div>
          <div class="action-row">
            <button id="saveQuestions" class="button primary" type="button">Save question config</button>
            <button id="exportQuestions" class="button" type="button">Export question config</button>
          </div>
        </div>
        <div class="panel">
          <h3>Custom questions</h3>
          <div class="field">
            <label for="questionTrack">Track</label>
            <select id="questionTrack" class="select">
              ${tracks().map((track) => `<option value="${track.key}" ${track.key === state.questionEditor.selectedTrack ? "selected" : ""}>${escapeHtml(track.label)}</option>`).join("")}
            </select>
          </div>
          <div class="list">
            ${custom
              .map(
                (item) => `
                  <div class="list-item custom-question-row">
                    <label class="field">
                      <span class="label">${escapeHtml(item.id)}</span>
                      <textarea class="textarea mini" data-custom-id="${escapeHtml(item.id)}">${escapeHtml(item.text)}</textarea>
                    </label>
                    <span class="pill">${escapeHtml(item.order)}</span>
                  </div>
                `,
              )
              .join("")}
          </div>
        </div>
      </section>
      ${messageHtml()}
    `,
  );
  document.querySelector("#traitSelect")?.addEventListener("change", (event) => {
    state.questionEditor.selectedTraitId = event.target.value;
    renderQuestions();
  });
  document.querySelector("#questionTrack")?.addEventListener("change", (event) => {
    state.questionEditor.selectedTrack = event.target.value;
    renderQuestions();
  });
  document.querySelector("#traitQuestionText")?.addEventListener("input", (event) => {
    const text = event.target.value.trim();
    state.overrides.trait_question_overrides ||= {};
    if (text && text !== selectedTrait.primary_question) {
      state.overrides.trait_question_overrides[selectedTrait.id] = text;
    } else {
      delete state.overrides.trait_question_overrides[selectedTrait.id];
    }
  });
  document.querySelectorAll("[data-custom-id]").forEach((textarea) => {
    textarea.addEventListener("input", () => {
      const items = state.overrides.custom_questions?.[state.questionEditor.selectedTrack] || [];
      const item = items.find((entry) => String(entry.id) === String(textarea.dataset.customId));
      if (item) {
        item.text = textarea.value;
      }
    });
  });
  document.querySelector("#saveQuestions")?.addEventListener("click", saveQuestionOverrides);
  document.querySelector("#exportQuestions")?.addEventListener("click", () => {
    downloadJson("web-question-config-preview.json", state.overrides);
  });
}

function renderHistory() {
  const rows = (Array.isArray(state.history) ? state.history : []).slice(0, 20);
  shell(
    "History",
    `
      <section class="panel">
        <h3>Recent interviews</h3>
        ${historyStatusTable(rows)}
      </section>
      ${messageHtml()}
    `,
  );
  bindHistoryStatusControls();
}

function historyStatusTable(rows) {
  return `
    <table class="table">
      <thead><tr><th>Date</th><th>Candidate</th><th>School</th><th>Track</th><th>Score</th><th>Determination</th><th>Offer</th></tr></thead>
      <tbody>
        ${rows
          .map(
            (row) => `
              <tr>
                <td>${escapeHtml(row.interview_date)}</td>
                <td>${escapeHtml(row.candidate_name)}</td>
                <td>${escapeHtml(row.school)}</td>
                <td>${escapeHtml(row.track)}</td>
                <td>${escapeHtml(row.interview_score)}</td>
                <td>${escapeHtml(row.determination)}</td>
                <td>
                  <select class="select compact" data-history-offer="${escapeHtml(historyRowKey(row))}">
                    ${["not_generated", "offer_generated", "offer_sent", "welcome_email_sent", "declined"]
                      .map((status) => `<option value="${status}" ${String(row.offer_status || "not_generated").toLowerCase() === status ? "selected" : ""}>${escapeHtml(status.replaceAll("_", " "))}</option>`)
                      .join("")}
                  </select>
                </td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function historyRowKey(row) {
  if (row.history_id) {
    return String(row.history_id);
  }
  const savedAt = String(row.saved_at || "").trim();
  if (!savedAt) {
    return "";
  }
  return `${String(row.candidate_name || "").trim().toLowerCase()}|${String(row.interview_date || "").trim()}|${savedAt}`;
}

function bindHistoryStatusControls() {
  document.querySelectorAll("[data-history-offer]").forEach((select) => {
    select.addEventListener("change", () => updateHistoryOfferStatus(select.dataset.historyOffer, select.value));
  });
}

function exportWebDraft() {
  downloadJson("web-app-draft.json", buildWebDraftPayload(state));
}

async function saveWebDraft() {
  const result = await saveWebDraftToBackend(buildWebDraftPayload(state));
  if (result.ok) {
    state.message = `Draft saved: ${result.payload.draft_name}`;
    state.messageType = "ok";
  } else {
    state.message = `${result.error} Use Export web draft instead.`;
    state.messageType = "error";
  }
  render();
}

async function saveQuestionOverrides() {
  const result = await saveQuestionOverridesToBackend(state.overrides);
  if (result.ok) {
    state.overrides = result.payload.overrides;
    state.message = "Question config saved.";
    state.messageType = "ok";
  } else {
    state.message = `${result.error} Use Export question config instead.`;
    state.messageType = "error";
  }
  renderQuestions();
}

async function saveOfferSettings() {
  updateOfferSettingsFromEditor();
  const result = await saveOfferSettingsToBackend(state.offerSettings);
  if (result.ok) {
    state.offerSettings = result.payload.offerSettings;
    state.message = "Offer settings saved.";
    state.messageType = "ok";
  } else {
    state.message = `${result.error} Keep Tk settings as source of truth if needed.`;
    state.messageType = "error";
  }
  renderSettings();
}

async function updateHistoryOfferStatus(rowKey, offerStatus) {
  const result = await updateHistoryOfferStatusToBackend(rowKey, offerStatus);
  if (result.ok) {
    state.history = Array.isArray(result.payload.history) ? result.payload.history : state.history;
    state.message = "Offer status updated.";
    state.messageType = "ok";
  } else {
    state.message = `${result.error} Static mode cannot update history.`;
    state.messageType = "error";
  }
  if (state.route === "onboarding") {
    renderOnboarding();
    return;
  }
  renderHistory();
}

async function finalizeWebDraft() {
  const result = await finalizeWebDraftToBackend(buildWebDraftPayload(state));
  if (result.ok) {
    state.finalizeResult = result.payload;
    state.scorePreview = result.payload.scorePreview || state.scorePreview;
    state.history = [result.payload.history_entry, ...(Array.isArray(state.history) ? state.history : [])];
    state.message = "Report finalized and history updated.";
    state.messageType = "ok";
  } else {
    state.message = `${result.error} Export the draft and finalize in Tk.`;
    state.messageType = "error";
  }
  renderReview();
}

async function resumeDraft(draftName) {
  const result = await loadWebDraftFromBackend(draftName);
  if (!result.ok) {
    state.message = `${result.error} Use Export web draft if needed.`;
    state.messageType = "error";
    renderStart();
    return;
  }
  applyDraftPayload(state, result.payload);
  await loadSignalsForCurrentFlow();
  routeTo("interview");
}

function render() {
  if (state.route === "candidate") {
    renderCandidate();
    return;
  }
  if (state.route === "interview") {
    renderInterview();
    return;
  }
  if (state.route === "review") {
    renderReview();
    return;
  }
  if (state.route === "onboarding") {
    renderOnboarding();
    return;
  }
  if (state.route === "settings") {
    renderSettings();
    return;
  }
  if (state.route === "questions") {
    renderQuestions();
    return;
  }
  if (state.route === "history") {
    renderHistory();
    return;
  }
  renderStart();
}

window.addEventListener("keydown", (event) => {
  const tagName = event.target?.tagName?.toLowerCase();
  const typing = tagName === "input" || tagName === "select" || tagName === "textarea";
  if (event.ctrlKey && event.key.toLowerCase() === "s") {
    event.preventDefault();
    exportWebDraft();
  }
  if (!typing && state.route === "interview" && ["1", "2", "3", "4", "5"].includes(event.key)) {
    const item = currentItem();
    if (item?.type === "trait") {
      traitResponse(item.id).raw_score = Number(event.key);
      renderInterview();
    }
  }
});

await loadData();
render();
