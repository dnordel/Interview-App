const rubricUrl = "../../config/rubric.json";
const weightedSignalsUrl = "../../Trait-Based%20Scoring/preschool_teacher_interview_signals_weighted.json";

const fallbackRubric = {
  traits: [
    {
      id: "trait_1",
      name: "Empathy & Respect for Children",
      priority: "Critical",
      weight: 3,
      primary_question:
        "Tell me about a time a child was having a hard moment emotionally. What did you notice, and how did you respond?",
      descriptors: {
        5: "Warm, respectful, emotionally insightful, child-centered.",
        4: "Respectful and supportive with some emotional insight.",
        3: "Appropriate but mostly behavior-management focused.",
        2: "Adult-centered, compliance-focused, limited empathy.",
        1: "Dismissive, controlling, or minimizing.",
      },
      sample_answers: {
        5: "Names emotion, validates child, co-regulates, reflects on impact.",
        4: "Helps child calm and rejoin with respectful language.",
        3: "Handles situation safely but with limited reflection.",
        2: "Focuses on compliance or adult convenience.",
        1: "Frames child as problem or dismisses feelings.",
      },
    },
  ],
  absolute_disqualifiers: [
    "Justifies yelling, shaming, intimidation, or force",
    "Minimizes children's rights or safety",
    "Cannot regulate emotions",
  ],
};

const app = document.querySelector("#app");
const state = {
  rubric: fallbackRubric,
  weightedSignals: null,
  signalDefinition: null,
  traitIndex: 0,
  responses: {},
  message: "",
  messageType: "",
};

async function loadJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load ${url}`);
  }
  return response.json();
}

async function loadState() {
  try {
    const [rubric, weightedSignals] = await Promise.all([loadJson(rubricUrl), loadJson(weightedSignalsUrl)]);
    state.rubric = rubric;
    state.weightedSignals = weightedSignals;
  } catch (_error) {
    state.message =
      "Using built-in sample data. Start a local server from the repo root to load rubric and weighted signals.";
    state.messageType = "error";
  }
  await loadSignals();
}

async function loadSignals() {
  const trait = currentTrait();
  state.signalDefinition = null;
  const traits = state.weightedSignals?.traits;
  if (!Array.isArray(traits)) {
    return;
  }
  state.signalDefinition =
    traits.find((item) => {
      const aliases = [item.trait_id, item.id, ...(item.trait_aliases || [])].filter(Boolean);
      return aliases.includes(trait.id);
    }) || null;
}

function currentTrait() {
  return state.rubric.traits[state.traitIndex] || state.rubric.traits[0];
}

function currentResponse() {
  const trait = currentTrait();
  if (!state.responses[trait.id]) {
    state.responses[trait.id] = {
      rawScore: null,
      absoluteDisqualifier: false,
      noExample: false,
      selectedSignals: new Set(),
      questionNotes: "",
      traitNotes: "",
      verbatimNotes: "",
    };
  }
  return state.responses[trait.id];
}

function scoreStatus() {
  const response = currentResponse();
  if (response.absoluteDisqualifier) {
    return "Score disabled because absolute disqualifier is checked.";
  }
  if (response.rawScore) {
    return `Selected score: ${response.rawScore}`;
  }
  return "Select 1-5 before continuing.";
}

function setRawScore(value) {
  const response = currentResponse();
  if (response.absoluteDisqualifier) {
    return;
  }
  response.rawScore = value;
  if (response.noExample && response.rawScore > 3) {
    response.rawScore = 3;
    state.message = "No usable example after follow-ups caps this score at 3.";
    state.messageType = "error";
  } else {
    state.message = "";
    state.messageType = "";
  }
  render();
}

function toggleNoExample(checked) {
  const response = currentResponse();
  response.noExample = checked;
  if (checked && response.rawScore && response.rawScore > 3) {
    response.rawScore = 3;
    state.message = "No usable example after follow-ups caps this score at 3.";
    state.messageType = "error";
  }
  render();
}

function toggleDisqualifier(checked) {
  const response = currentResponse();
  response.absoluteDisqualifier = checked;
  if (checked) {
    response.rawScore = null;
  }
  render();
}

function validate() {
  const response = currentResponse();
  if (!response.absoluteDisqualifier && !response.rawScore) {
    state.message = "A raw score is missing. Select 1-5, or check absolute disqualifier.";
    state.messageType = "error";
    render();
    document.querySelector(".score-button")?.focus();
    return false;
  }
  if (response.absoluteDisqualifier && !response.verbatimNotes.trim()) {
    state.message = "Absolute disqualifier needs supporting quote or notes.";
    state.messageType = "error";
    render();
    document.querySelector("#verbatimNotes")?.focus();
    return false;
  }
  state.message = state.traitIndex === state.rubric.traits.length - 1 ? "All scored competencies reviewed." : "Question ready to continue.";
  state.messageType = "ok";
  render();
  return true;
}

function navigateToTrait(nextIndex) {
  const bounded = Math.max(0, Math.min(state.rubric.traits.length - 1, nextIndex));
  if (bounded === state.traitIndex) {
    return;
  }
  state.traitIndex = bounded;
  state.message = "";
  state.messageType = "";
  loadSignals().then(render);
}

function continueFlow() {
  if (!validate()) {
    return;
  }
  if (state.traitIndex < state.rubric.traits.length - 1) {
    navigateToTrait(state.traitIndex + 1);
  }
}

function serializedResponses() {
  return Object.fromEntries(
    Object.entries(state.responses).map(([traitId, response]) => [
      traitId,
      {
        trait_id: traitId,
        raw_score: response.absoluteDisqualifier ? null : response.rawScore,
        absolute_disqualifier: response.absoluteDisqualifier,
        no_example_after_followups: response.noExample,
        selected_signal_ids: [...response.selectedSignals],
        question_notes: response.questionNotes,
        trait_notes: response.traitNotes,
        verbatim_notes: response.verbatimNotes,
      },
    ]),
  );
}

function downloadDraft() {
  const payload = {
    exported_at: new Date().toISOString(),
    current_trait_id: currentTrait().id,
    trait_inputs: serializedResponses(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `scored-question-web-draft.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
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

function signalsHtml() {
  const response = currentResponse();
  const signals = [
    ...(state.signalDefinition?.core_signals ?? []),
    ...(state.signalDefinition?.extended_signals ?? []),
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
                response.selectedSignals.has(id) ? "checked" : ""
              } />
              <span>${escapeHtml(signal.label || id)}</span>
            </label>
          `;
        })
        .join("")}
    </div>
  `;
}

function disqualifiersHtml() {
  const items = state.rubric.absolute_disqualifiers ?? [];
  return `
    <details class="accordion">
      <summary>Global disqualifiers</summary>
      <div class="anchor-list">
        ${items
          .map(
            (item, index) => `
              <div class="anchor-item">
                <span class="anchor-num">${index + 1}</span>
                <span>${escapeHtml(item)}</span>
              </div>
            `,
          )
          .join("")}
      </div>
    </details>
  `;
}

function render() {
  const trait = currentTrait();
  const response = currentResponse();
  const total = state.rubric.traits.length || 1;
  const progress = Math.round(((state.traitIndex + 1) / total) * 100);
  app.innerHTML = `
    <section class="topbar">
      <h1>Structured Preschool Interview Tool</h1>
      <div class="topbar-actions">
        <label>
          <span class="helper">Competency</span>
          <select id="traitSelect" class="select">
            ${state.rubric.traits
              .map(
                (item, index) => `
                  <option value="${index}" ${index === state.traitIndex ? "selected" : ""}>
                    ${index + 1}. ${escapeHtml(item.name)}
                  </option>
                `,
              )
              .join("")}
          </select>
        </label>
      </div>
    </section>

    <section class="question-header">
      <div class="meta-row">
        <span>Scored competency - Question ${state.traitIndex + 1} of ${total}</span>
        <span>
          <span class="pill ${trait.priority === "Critical" ? "critical" : ""}">${escapeHtml(trait.priority)}</span>
          <span class="pill weight">Weight x${escapeHtml(trait.weight)}</span>
        </span>
      </div>
      <h2>${escapeHtml(trait.name)}</h2>
      <p class="question-text"><strong>Primary question:</strong> ${escapeHtml(trait.primary_question)}</p>
      <div class="progress" aria-label="Interview progress"><span style="width:${progress}%"></span></div>
    </section>

    <section class="workspace">
      <div>
        <section class="panel">
          <h3>Candidate notes</h3>
          <p class="helper">Capture concrete evidence and follow-ups during the live interview.</p>
          <label>
            <span class="helper">Question notes</span>
            <textarea id="questionNotes" class="textarea">${escapeHtml(response.questionNotes)}</textarea>
          </label>
          <label>
            <span class="helper">Scored competency notes for final report</span>
            <textarea id="traitNotes" class="textarea short">${escapeHtml(response.traitNotes)}</textarea>
          </label>
        </section>

        <section class="panel">
          <h3>Trait observations</h3>
          <p class="helper">Mark observed evidence. These selections stay separate from raw score.</p>
          ${signalsHtml()}
        </section>
      </div>

      <aside>
        <section class="panel decision-panel">
          <h3>Raw score</h3>
          <p class="helper">Required unless absolute disqualifier is checked.</p>
          <div class="score-grid" role="group" aria-label="Raw score">
            ${[1, 2, 3, 4, 5]
              .map(
                (score) => `
                  <button class="score-button" data-score="${score}" aria-pressed="${
                    response.rawScore === score
                  }" ${response.absoluteDisqualifier ? "disabled" : ""}>${score}</button>
                `,
              )
              .join("")}
          </div>
          <div class="status-line">${escapeHtml(scoreStatus())}</div>
        </section>

        <section class="panel danger-panel">
          <div class="dq-row">
            <label><input id="dqToggle" type="checkbox" ${
              response.absoluteDisqualifier ? "checked" : ""
            } /> Absolute disqualifier observed</label>
            <button id="showGlobal" class="button danger" type="button">Review list</button>
          </div>
          <div class="quote-box" ${response.absoluteDisqualifier ? "" : "hidden"}>
            <label>
              <span class="helper">Verbatim quote/notes required</span>
              <textarea id="verbatimNotes" class="textarea short">${escapeHtml(response.verbatimNotes)}</textarea>
            </label>
          </div>
        </section>

        <section class="panel">
          <h3>No usable example</h3>
          <p class="helper">Probe neutrally before scoring. If still no example after follow-ups, cap score at 3.</p>
          <div class="no-example-row">
            <label><input id="noExample" type="checkbox" ${response.noExample ? "checked" : ""} /> Cap score at 3</label>
          </div>
        </section>

        <section class="panel">
          <h3>Reference</h3>
          ${anchorsHtml("Scoring descriptors", trait.descriptors)}
          ${anchorsHtml("Sample answers", trait.sample_answers)}
          ${disqualifiersHtml()}
        </section>

        ${state.message ? `<div class="message ${state.messageType}">${escapeHtml(state.message)}</div>` : ""}
      </aside>
    </section>

    <section class="actions">
      <div class="action-group">
        <button id="backButton" class="button" type="button" ${state.traitIndex === 0 ? "disabled" : ""}>Back</button>
        <button id="downloadDraft" class="button" type="button">Save draft JSON</button>
      </div>
      <div class="action-group">
        <button class="button" type="button">Play audio</button>
        <button id="continueButton" class="button primary" type="button">${
          state.traitIndex === total - 1 ? "Review complete" : "Continue"
        }</button>
      </div>
    </section>
  `;
  bindEvents();
}

function bindEvents() {
  document.querySelector("#traitSelect")?.addEventListener("change", async (event) => {
    state.traitIndex = Number(event.target.value);
    state.message = "";
    state.messageType = "";
    await loadSignals();
    render();
  });
  document.querySelectorAll("[data-score]").forEach((button) => {
    button.addEventListener("click", () => setRawScore(Number(button.dataset.score)));
  });
  document.querySelector("#dqToggle")?.addEventListener("change", (event) => toggleDisqualifier(event.target.checked));
  document.querySelector("#noExample")?.addEventListener("change", (event) => toggleNoExample(event.target.checked));
  document.querySelector("#questionNotes")?.addEventListener("input", (event) => {
    currentResponse().questionNotes = event.target.value;
  });
  document.querySelector("#traitNotes")?.addEventListener("input", (event) => {
    currentResponse().traitNotes = event.target.value;
  });
  document.querySelector("#verbatimNotes")?.addEventListener("input", (event) => {
    currentResponse().verbatimNotes = event.target.value;
  });
  document.querySelectorAll("[data-signal]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const response = currentResponse();
      if (checkbox.checked) {
        response.selectedSignals.add(checkbox.dataset.signal);
      } else {
        response.selectedSignals.delete(checkbox.dataset.signal);
      }
    });
  });
  document.querySelector("#backButton")?.addEventListener("click", () => navigateToTrait(state.traitIndex - 1));
  document.querySelector("#continueButton")?.addEventListener("click", continueFlow);
  document.querySelector("#downloadDraft")?.addEventListener("click", downloadDraft);
  document.querySelector("#showGlobal")?.addEventListener("click", () => {
    document.querySelector("details.accordion:last-of-type")?.setAttribute("open", "");
  });
}

window.addEventListener("keydown", (event) => {
  const target = event.target;
  const tagName = target?.tagName?.toLowerCase();
  const typing = tagName === "textarea" || tagName === "input" || tagName === "select";
  if (event.ctrlKey && event.key.toLowerCase() === "s") {
    event.preventDefault();
    downloadDraft();
    return;
  }
  if (event.ctrlKey && event.key === "Enter") {
    event.preventDefault();
    continueFlow();
    return;
  }
  if (!typing && ["1", "2", "3", "4", "5"].includes(event.key)) {
    setRawScore(Number(event.key));
  }
});

await loadState();
render();
