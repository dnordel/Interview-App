export const urls = {
  rubric: "../../config/rubric.json",
  overrides: "../../config/question_overrides.json",
  history: "../../interview_history.json",
  offerSettings: "../../school_offer_settings.json",
  weightedSignals: "../../Trait-Based%20Scoring/preschool_teacher_interview_signals_weighted.json",
};

export const fallbackRubric = {
  tracks: {
    preschool: { label: "Preschool" },
    infant_toddler: { label: "Infant/Toddler" },
    behavior_support_specialist: { label: "Behavior Support Specialist / Early Childhood Teacher" },
  },
  traits: [
    {
      id: "trait_1",
      name: "Empathy & Respect for Children",
      priority: "Critical",
      weight: 3,
      applicable_tracks: ["all"],
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

let weightedSignalsCache = null;

export const fallbackOverrides = {
  custom_questions: {
    preschool: [
      { id: "Why-ECE", text: "Tell me about yourself and why early childhood education matters to you.", order: 1 },
      { id: "Why-LPL", text: "Why are you applying to Launch Pad Learning?", order: 2 },
    ],
  },
  track_question_flow: {
    preschool: [
      { type: "custom", id: "Why-ECE" },
      { type: "custom", id: "Why-LPL" },
      { type: "trait", id: "trait_1" },
    ],
  },
  trait_question_overrides: {},
};

export function createInitialState(today = new Date().toISOString().slice(0, 10)) {
  return {
    route: "start",
    rubric: fallbackRubric,
    overrides: fallbackOverrides,
    history: [],
    offerSettings: {},
    drafts: [],
    signalDefinition: null,
    scorePreview: null,
    finalizeResult: null,
    flowRecordings: [],
    flowIndex: 0,
    candidate: {
      candidate_name: "",
      interview_date: today,
      school: "",
      track: "preschool",
    },
    traitInputs: {},
    customInputs: {},
    questionEditor: {
      selectedTraitId: "trait_1",
      selectedTrack: "preschool",
    },
    message: "",
    messageType: "",
  };
}

export async function loadJson(url, fallback) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Unable to load ${url}`);
    }
    return await response.json();
  } catch (_error) {
    return fallback;
  }
}

export async function loadApplicationData() {
  const apiPayload = await loadJson("/api/bootstrap", null);
  if (apiPayload && typeof apiPayload === "object") {
    return {
      rubric: apiPayload.rubric || fallbackRubric,
      overrides: apiPayload.overrides || fallbackOverrides,
      history: Array.isArray(apiPayload.history) ? apiPayload.history : [],
      offerSettings: apiPayload.offerSettings || {},
    };
  }
  const [rubric, overrides, history, offerSettings] = await Promise.all([
    loadJson(urls.rubric, fallbackRubric),
    loadJson(urls.overrides, fallbackOverrides),
    loadJson(urls.history, []),
    loadJson(urls.offerSettings, {}),
  ]);
  return { rubric, overrides, history, offerSettings };
}

export async function loadDraftsFromBackend() {
  const apiPayload = await loadJson("/api/drafts", null);
  if (!apiPayload || !Array.isArray(apiPayload.drafts)) {
    return { ok: false, drafts: [] };
  }
  return { ok: true, drafts: apiPayload.drafts };
}

export async function loadWebDraftFromBackend(draftName) {
  const encoded = encodeURIComponent(draftName);
  const apiPayload = await loadJson(`/api/drafts/${encoded}`, null);
  if (!apiPayload || !apiPayload.draft) {
    return { ok: false, error: "Draft API is not available from this server." };
  }
  return { ok: true, payload: apiPayload.draft };
}

export function applyDraftPayload(state, payload) {
  const candidate = payload?.candidate || {};
  state.candidate = {
    candidate_name: String(candidate.name || candidate.candidate_name || ""),
    interview_date: String(candidate.interview_date || state.candidate.interview_date || ""),
    school: String(candidate.school || ""),
    track: String(candidate.track || state.candidate.track || "preschool"),
  };
  state.flowIndex = Number.isInteger(payload?.current_index) ? Math.max(0, payload.current_index) : 0;
  state.traitInputs = payload?.trait_inputs && typeof payload.trait_inputs === "object" ? payload.trait_inputs : {};
  state.customInputs = payload?.custom_inputs && typeof payload.custom_inputs === "object" ? payload.custom_inputs : {};
  state.flowRecordings = Array.isArray(payload?.flow_recordings) ? payload.flow_recordings : [];
  state.questionEditor.selectedTrack = state.candidate.track;
}

export async function loadSignalDefinition(item) {
  if (!item || item.type !== "trait") {
    return null;
  }
  if (!weightedSignalsCache) {
    weightedSignalsCache = await loadJson(urls.weightedSignals, null);
  }
  const traits = weightedSignalsCache?.traits;
  if (!Array.isArray(traits)) {
    return null;
  }
  return traits.find((trait) => {
    const aliases = [trait.trait_id, trait.id, ...(trait.trait_aliases || [])].filter(Boolean);
    return aliases.includes(item.id);
  }) || null;
}

export function applyApplicationData(state, data) {
  state.rubric = data.rubric;
  state.overrides = data.overrides;
  state.history = data.history;
  state.offerSettings = data.offerSettings;
  state.candidate.track = Object.keys(state.rubric.tracks || {})[0] || "preschool";
  state.questionEditor.selectedTrack = state.candidate.track;
  state.questionEditor.selectedTraitId = state.rubric.traits?.[0]?.id || "trait_1";
}

export function tracksFromRubric(rubric) {
  return Object.entries(rubric.tracks || {}).map(([key, value]) => ({
    key,
    label: value?.label || key,
  }));
}

export function traitsForTrack(rubric, track) {
  return (rubric.traits || []).filter((trait) => {
    const applicable = trait.applicable_tracks || ["all"];
    return applicable.includes("all") || applicable.includes(track);
  });
}

export function customQuestionsForTrack(overrides, track) {
  return [...(overrides.custom_questions?.[track] || [])].sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
}

export function flowForTrack(rubric, overrides, track) {
  const configured = overrides.track_question_flow?.[track];
  if (Array.isArray(configured) && configured.length) {
    return configured;
  }
  return [
    ...customQuestionsForTrack(overrides, track).map((item) => ({ type: "custom", id: item.id })),
    ...traitsForTrack(rubric, track).map((item) => ({ type: "trait", id: item.id })),
  ];
}

export function traitById(rubric, traitId) {
  return (rubric.traits || []).find((trait) => trait.id === traitId);
}

export function customById(overrides, track, customId) {
  return customQuestionsForTrack(overrides, track).find((item) => item.id === customId);
}

export function primaryQuestion(overrides, trait) {
  const override = overrides.trait_question_overrides?.[trait.id];
  return String(override || trait.primary_question || "");
}

export function createTraitResponse() {
  return {
    raw_score: null,
    absolute_disqualifier: false,
    no_example_after_followups: false,
    selected_signal_ids: [],
    question_notes: "",
    trait_notes: "",
    verbatim_notes: "",
  };
}

export function createCustomResponse() {
  return { answer: "", skipped: false };
}

export function buildWebDraftPayload(state, exportedAt = new Date().toISOString()) {
  return {
    exported_at: exportedAt,
    candidate: state.candidate,
    current_route: state.route,
    current_flow_index: state.flowIndex,
    trait_inputs: state.traitInputs,
    custom_inputs: state.customInputs,
    flow_recordings: state.flowRecordings,
  };
}

export async function saveWebDraftToBackend(payload) {
  try {
    const response = await fetch("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return { ok: false, error: "Draft API did not accept the save request." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "Draft API is not available from this server." };
  }
}

export async function uploadWebRecordingToBackend(payload) {
  try {
    const response = await fetch("/api/recordings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return { ok: false, error: "Recording API did not accept the audio." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "Recording API is not available from this server." };
  }
}

export async function saveQuestionOverridesToBackend(payload) {
  try {
    const response = await fetch("/api/question-overrides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return { ok: false, error: "Question override API did not accept the save request." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "Question override API is not available from this server." };
  }
}

export async function saveOfferSettingsToBackend(payload) {
  try {
    const response = await fetch("/api/offer-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return { ok: false, error: "Offer settings API did not accept the save request." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "Offer settings API is not available from this server." };
  }
}

export async function updateHistoryOfferStatusToBackend(rowKey, offerStatus) {
  try {
    const encoded = encodeURIComponent(rowKey);
    const response = await fetch(`/api/history/${encoded}/offer-status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offer_status: offerStatus }),
    });
    if (!response.ok) {
      return { ok: false, error: "History API did not accept the update request." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "History API is not available from this server." };
  }
}

export async function scoreWebDraftPreview(payload) {
  try {
    const response = await fetch("/api/score-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return { ok: false, error: "Score preview API did not accept the draft." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "Score preview API is not available from this server." };
  }
}

export async function finalizeWebDraftToBackend(payload) {
  try {
    const response = await fetch("/api/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return { ok: false, error: "Finalize API did not accept the draft." };
    }
    return { ok: true, payload: await response.json() };
  } catch (_error) {
    return { ok: false, error: "Finalize API is not available from this server." };
  }
}
