const btnStart = document.getElementById('btnStart');
const btnStop = document.getElementById('btnStop');
const recIndicator = document.getElementById('recIndicator');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const includeRewritesEl = document.getElementById('includeRewrites');
const setupBanner = document.getElementById('setupBanner');

let mediaRecorder = null;
let audioChunks = [];
let currentTtsAudio = null;

// Guided mode state
let guidedState = {
  mode: 'setup', // 'setup' | 'active' | 'feedback' | 'complete'
  questions: [],
  currentQuestionIndex: 0,
  jobDescription: '',
  sessionResults: [],
};

async function speakQuestion(text) {
  if (!text) return;
  // Try Edge TTS first (better voice quality)
  const voiceSelect = document.getElementById('voiceSelect');
  const edgeVoice = voiceSelect?.value || 'en-US-JennyNeural';
  try {
    const res = await fetch(`/api/tts?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(edgeVoice)}`);
    if (res.ok) {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      currentTtsAudio = audio;
      await new Promise((resolve, reject) => {
        audio.onended = () => { currentTtsAudio = null; URL.revokeObjectURL(url); resolve(); };
        audio.onerror = () => { currentTtsAudio = null; reject(); };
        audio.play();
      });
      return;
    }
  } catch {
    /* fall through to Web Speech API */
  }
  // Fallback: Web Speech API
  if (!window.speechSynthesis) return;
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.9;
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    speechSynthesis.speak(utterance);
  });
}

function populateVoiceSelect() {
  /* Edge TTS voices are in the HTML */
}

function stopSpeaking() {
  if (currentTtsAudio) {
    currentTtsAudio.pause();
    currentTtsAudio.currentTime = 0;
    currentTtsAudio = null;
  }
  if (window.speechSynthesis) {
    speechSynthesis.cancel();
  }
}

async function checkSetup() {
  try {
    const res = await fetch('/api/check');
    const data = await res.json();
    const missing = [];
    if (!data.ffmpeg) missing.push('ffmpeg (brew install ffmpeg)');
    if (!data.llm_ready) {
      if (data.gemini_configured) {
        missing.push('Gemini API key set but request may be failing');
      } else {
        missing.push(`LLM: Set GEMINI_API_KEY for cloud, or run Ollama (ollama serve && ollama pull ${data.model || 'llama3.2'})`);
      }
    }
    if (missing.length) {
      setupBanner.textContent = 'Before recording: ' + missing.join('. ');
      setupBanner.className = 'setup-banner warn';
      setupBanner.style.display = 'block';
    } else {
      const provider = data.gemini_configured ? 'Gemini' : 'Ollama';
      setupBanner.textContent = `Ready: ffmpeg and ${provider} detected.`;
      setupBanner.className = 'setup-banner ok';
      setupBanner.style.display = 'block';
    }
  } catch {
    setupBanner.textContent = 'Could not reach server. Start with: cd backend && uvicorn main:app --port 8000';
    setupBanner.className = 'setup-banner warn';
    setupBanner.style.display = 'block';
  }
}

function setStatus(msg, type = '') {
  statusEl.textContent = msg;
  statusEl.className = 'status' + (type ? ` ${type}` : '');
  statusEl.style.display = msg ? 'block' : 'none';
}

function clearStatus() {
  setStatus('');
}

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatus(
      'Microphone access requires a secure context. Open this app via http://localhost:8000 (run: uvicorn main:app --port 8000 from the backend folder).',
      'error'
    );
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(audioChunks, { type: 'audio/webm' });
      if (blob.size > 0) {
        uploadAndAnalyze(blob);
      } else {
        setStatus('No audio captured. Record for at least a few seconds.', 'error');
      }
    };

    mediaRecorder.start(500);
    recIndicator.classList.add('active');
    btnStart.disabled = true;
    btnStop.disabled = false;
    resultsEl.innerHTML = '';
    clearStatus();
  } catch (err) {
    setStatus('Microphone access denied: ' + err.message, 'error');
  }
}

function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
  mediaRecorder.stop();
  recIndicator.classList.remove('active');
  btnStart.disabled = false;
  btnStop.disabled = true;
}

async function uploadAndAnalyze(blob, options = {}) {
  setStatus('Transcribing and scoring...', 'loading');
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');
  if (options.questionText) formData.append('question_text', options.questionText);
  if (options.jobDescription) formData.append('job_description', options.jobDescription);
  const includeRewrites = options.includeRewrites ?? includeRewritesEl?.checked ?? true;
  const params = new URLSearchParams({ include_rewrites: includeRewrites });

  try {
    const res = await fetch(`/api/analyze?${params}`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      let msg = res.statusText;
      try {
        const j = JSON.parse(text);
        const d = j.detail;
        msg = Array.isArray(d) ? d.map((x) => x.msg ?? x).join(', ') : (d ?? msg);
      } catch {
        msg = text || msg;
      }
      throw new Error(msg);
    }
    const data = await res.json();
    if (options.onSuccess) {
      options.onSuccess(data);
    } else {
      renderResults(data);
    }
    clearStatus();
  } catch (err) {
    setStatus('Error: ' + err.message, 'error');
    if (options.onSuccess) options.onSuccess(null);
  }
}

function renderResults(data) {
  const { segments, scores, rewrites } = data;
  let html = '';

  if (segments && segments.length > 0) {
    html += '<div class="section"><h2>Transcript</h2><div class="transcript">';
    segments.forEach((s, i) => {
      html += `<div class="turn"><strong>Turn ${i + 1}</strong> [${s.start.toFixed(1)}s–${s.end.toFixed(1)}s]<br><span class="turn-text">${escapeHtml(s.text)}</span></div>`;
    });
    html += '</div></div>';
  }

  if (scores && scores.turns && scores.turns.length > 0) {
    html += '<div class="section"><h2>Scores & Feedback</h2>';
    if (scores.overall_summary) {
      html += `<p style="margin-bottom:1rem;color:var(--muted)">${escapeHtml(scores.overall_summary)}</p>`;
    }
    scores.turns.forEach((t) => {
      html += renderTurnScore(t);
    });
    html += '</div>';
  }

  if (rewrites && rewrites.length > 0) {
    html += '<div class="section"><h2>Rewrite Suggestions</h2>';
    rewrites.forEach((r) => {
      html += `<div class="turn"><strong>Turn ${r.turn_index + 1}</strong><br>`;
      if (r.tight_45s) {
        html += `<div class="rewrite-block"><h4>45-second version</h4><p>${escapeHtml(r.tight_45s)}</p></div>`;
      }
      if (r.expanded_2min) {
        html += `<div class="rewrite-block"><h4>2-minute version</h4><p>${escapeHtml(r.expanded_2min)}</p></div>`;
      }
      html += '</div>';
    });
    html += '</div>';
  }

  if (!html) html = '<p style="color:var(--muted)">No results.</p>';
  resultsEl.innerHTML = html;
}

function renderTurnScore(t) {
  const met = (o) => (o && typeof o.met === 'boolean' ? o.met : null);
  const note = (o) => (o && o.note ? o.note : '');
  const badge = (m) => {
    if (m === null) return '<span class="badge">—</span>';
    return m ? '<span class="badge yes">Y</span>' : '<span class="badge no">N</span>';
  };

  const items = [
    ['Direct answer (10s)', met(t.direct_answer_10s), note(t.direct_answer_10s)],
    ['Specific example', met(t.specific_example), note(t.specific_example)],
    ['Quantified impact', met(t.quantified_impact), note(t.quantified_impact)],
    ['Tradeoffs', met(t.tradeoffs), note(t.tradeoffs)],
    ['Crisp takeaway', met(t.crisp_takeaway), note(t.crisp_takeaway)],
  ];

  let html = `<div class="turn"><div class="turn-text">${escapeHtml(t.text || '')}</div>`;
  html += `<div class="rubric">`;
  items.forEach(([label, m, n]) => {
    html += `<div class="rubric-item">${badge(m)} ${label}${n ? ': ' + escapeHtml(n) : ''}</div>`;
  });
  html += `<div class="rubric-item">Filler count: ${t.filler_count ?? '—'}</div>`;
  html += `<div class="rubric-item">Long pauses: ${t.long_pauses ?? '—'}</div>`;
  html += `<div class="rubric-item">Trailing/ramble: ${badge(t.trailing_sentences)}</div>`;
  if (t.pace_wpm != null) {
    const paceLabel = t.pace_rating ? t.pace_rating.replace(/_/g, ' ') : '—';
    html += `<div class="rubric-item">Pace: ${t.pace_wpm} WPM (${paceLabel})${t.pace_feedback ? ': ' + escapeHtml(t.pace_feedback) : ''}</div>`;
  }
  if (t.relevance_to_role && (t.relevance_to_role.met != null || t.relevance_to_role.note)) {
    html += `<div class="rubric-item">Relevance to role: ${badge(t.relevance_to_role.met)}${t.relevance_to_role.note ? ' ' + escapeHtml(t.relevance_to_role.note) : ''}</div>`;
  }
  html += `<div class="rubric-item">Question type: ${escapeHtml(t.question_type || '—')}</div>`;
  if (t.actionable_feedback) {
    html += `<div class="rubric-item" style="margin-top:0.5rem"><strong>Feedback:</strong> ${escapeHtml(t.actionable_feedback)}</div>`;
  }
  html += '</div></div>';
  return html;
}

function escapeHtml(s) {
  if (!s) return '';
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

btnStart.addEventListener('click', startRecording);
btnStop.addEventListener('click', stopRecording);

// --- Guided mode ---
const guidedSetupEl = document.getElementById('guidedSetup');
const guidedActiveEl = document.getElementById('guidedActive');
const freeformSectionEl = document.getElementById('freeformSection');
const jobDescriptionEl = document.getElementById('jobDescription');
const questionListEl = document.getElementById('questionList');
const addQuestionInputEl = document.getElementById('addQuestionInput');
const btnLoadQuestions = document.getElementById('btnLoadQuestions');
const btnAddQuestion = document.getElementById('btnAddQuestion');
const btnStartGuided = document.getElementById('btnStartGuided');
const questionProgressEl = document.getElementById('questionProgress');
const currentQuestionEl = document.getElementById('currentQuestion');
const btnPlayQuestion = document.getElementById('btnPlayQuestion');
const btnStartAnswer = document.getElementById('btnStartAnswer');
const btnStopAnswer = document.getElementById('btnStopAnswer');
const guidedFeedbackEl = document.getElementById('guidedFeedback');
const btnTryAgain = document.getElementById('btnTryAgain');
const btnNextQuestion = document.getElementById('btnNextQuestion');
const sessionSummaryEl = document.getElementById('sessionSummary');
const btnModeGuided = document.getElementById('btnModeGuided');
const btnModeFreeform = document.getElementById('btnModeFreeform');

function setMode(mode) {
  if (mode === 'guided') {
    btnModeGuided.classList.add('active');
    btnModeFreeform.classList.remove('active');
    guidedSetupEl.style.display = 'block';
    guidedActiveEl.style.display = 'none';
    freeformSectionEl.style.display = 'none';
    sessionSummaryEl.style.display = 'none';
    resultsEl.style.display = 'none';
  } else {
    btnModeGuided.classList.remove('active');
    btnModeFreeform.classList.add('active');
    guidedSetupEl.style.display = 'none';
    guidedActiveEl.style.display = 'none';
    freeformSectionEl.style.display = 'block';
    sessionSummaryEl.style.display = 'none';
    resultsEl.style.display = 'block';
  }
}

function renderQuestionList() {
  const qs = guidedState.questions;
  if (!qs.length) {
    questionListEl.innerHTML = '<p class="muted">Load questions first.</p>';
    return;
  }
  questionListEl.innerHTML = '<ol>' + qs.map((q) => `<li>${escapeHtml(q)}</li>`).join('') + '</ol>';
  btnStartGuided.disabled = false;
}

async function loadQuestions() {
  const jd = jobDescriptionEl?.value?.trim() || '';
  setStatus('Loading questions...', 'loading');
  try {
    let res;
    if (jd) {
      res = await fetch('/api/adapt-questions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_description: jd }),
      });
    } else {
      res = await fetch('/api/questions');
    }
    const data = await res.json();
    guidedState.questions = data.questions || [];
    guidedState.jobDescription = jd;
    renderQuestionList();
    clearStatus();
  } catch (err) {
    setStatus('Error: ' + err.message, 'error');
  }
}

function addCustomQuestion() {
  const q = addQuestionInputEl?.value?.trim();
  if (!q) return;
  guidedState.questions.push(q);
  addQuestionInputEl.value = '';
  renderQuestionList();
}

function startGuidedInterview() {
  if (!guidedState.questions.length) return;
  guidedState.mode = 'active';
  guidedState.currentQuestionIndex = 0;
  guidedState.sessionResults = [];
  guidedSetupEl.style.display = 'none';
  guidedActiveEl.style.display = 'block';
  guidedFeedbackEl.innerHTML = '';
  btnTryAgain.style.display = 'none';
  btnNextQuestion.style.display = 'none';
  showCurrentQuestion();
}

function showCurrentQuestion() {
  const q = guidedState.questions[guidedState.currentQuestionIndex];
  const n = guidedState.questions.length;
  questionProgressEl.textContent = `Question ${guidedState.currentQuestionIndex + 1} of ${n}`;
  currentQuestionEl.textContent = q;
  btnStartAnswer.disabled = true;
  if (guidedState.mode === 'active') {
    speakQuestion(q).then(() => {
      btnStartAnswer.disabled = false;
    });
  } else {
    btnStartAnswer.disabled = false;
  }
}

function onGuidedStartRecording() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatus('Microphone access requires a secure context.', 'error');
    return;
  }
  stopSpeaking();
  navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(audioChunks, { type: 'audio/webm' });
      if (blob.size > 0) {
        const q = guidedState.questions[guidedState.currentQuestionIndex];
        uploadAndAnalyze(blob, {
          questionText: q,
          jobDescription: guidedState.jobDescription || undefined,
          includeRewrites: true,
          onSuccess: (data) => onGuidedAnalyzeResult(data),
        });
      } else {
        setStatus('No audio captured. Record for at least a few seconds.', 'error');
        guidedState.mode = 'feedback';
        showGuidedFeedback();
      }
    };
    mediaRecorder.start(500);
    const recInd = document.getElementById('guidedRecIndicator');
    if (recInd) recInd.classList.add('active');
    btnStartAnswer.disabled = true;
    btnStopAnswer.disabled = false;
  }).catch((err) => setStatus('Microphone access denied: ' + err.message, 'error'));
}

function onGuidedStopRecording() {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
  mediaRecorder.stop();
  const recInd = document.getElementById('guidedRecIndicator');
  if (recInd) recInd.classList.remove('active');
  btnStartAnswer.disabled = true;
  btnStopAnswer.disabled = false;
  guidedState.mode = 'analyzing';
}

function onGuidedAnalyzeResult(data) {
  guidedState.mode = 'feedback';
  if (data) {
    guidedState.sessionResults[guidedState.currentQuestionIndex] = data;
  }
  showGuidedFeedback();
}

function showGuidedFeedback() {
  const data = guidedState.sessionResults[guidedState.currentQuestionIndex];
  if (data && data.scores && data.scores.turns && data.scores.turns.length > 0) {
    let html = '<div class="section"><h2>Feedback</h2>';

    // Full transcript - show everything that was transcribed (no truncation)
    if (data.segments && data.segments.length > 0) {
      const fullTranscript = data.segments.map((s) => s.text).join(' ').trim();
      html += '<div class="transcript-block"><h3>Full transcript</h3>';
      html += `<p class="turn-text transcript-full">${escapeHtml(fullTranscript) || '(no speech detected)'}</p>`;
      html += `<p class="muted" style="font-size:0.8rem;margin-top:0.25rem">${data.segments.length} segment(s), ${fullTranscript.split(/\s+/).filter(Boolean).length} words</p>`;
      html += '</div>';
    }

    if (data.scores.overall_summary) {
      html += `<p style="margin-bottom:1rem;color:var(--muted)">${escapeHtml(data.scores.overall_summary)}</p>`;
    }
    data.scores.turns.forEach((t) => { html += renderTurnScore(t); });
    if (data.rewrites && data.rewrites.length > 0) {
      html += '<h3 style="margin-top:1rem">Rewrite suggestions</h3>';
      data.rewrites.forEach((r) => {
        html += `<div class="turn">`;
        if (r.tight_45s) html += `<div class="rewrite-block"><h4>45-second version</h4><p>${escapeHtml(r.tight_45s)}</p></div>`;
        if (r.expanded_2min) html += `<div class="rewrite-block"><h4>2-minute version</h4><p>${escapeHtml(r.expanded_2min)}</p></div>`;
        html += '</div>';
      });
    }
    html += '</div>';
    guidedFeedbackEl.innerHTML = html;
  } else {
    guidedFeedbackEl.innerHTML = '<p class="muted">No feedback available.</p>';
  }
  btnTryAgain.style.display = 'inline-block';
  btnNextQuestion.style.display = 'inline-block';
  btnStartAnswer.disabled = true;
}

function onTryAgain() {
  guidedState.mode = 'active';
  guidedFeedbackEl.innerHTML = '';
  btnTryAgain.style.display = 'none';
  btnNextQuestion.style.display = 'none';
  showCurrentQuestion();
}

function onNextQuestion() {
  guidedState.currentQuestionIndex++;
  if (guidedState.currentQuestionIndex >= guidedState.questions.length) {
    showSessionSummary();
    return;
  }
  guidedState.mode = 'active';
  guidedFeedbackEl.innerHTML = '';
  btnTryAgain.style.display = 'none';
  btnNextQuestion.style.display = 'none';
  showCurrentQuestion();
}

function showSessionSummary() {
  guidedState.mode = 'complete';
  guidedActiveEl.style.display = 'none';
  sessionSummaryEl.style.display = 'block';
  const results = guidedState.sessionResults.filter(Boolean);
  let html = '<h2>Session complete</h2><p>You answered ' + results.length + ' question(s).</p>';
  results.forEach((r, i) => {
    const q = guidedState.questions[i] || 'Question ' + (i + 1);
    html += `<div class="turn"><strong>${escapeHtml(q)}</strong><br>`;
    if (r.scores?.overall_summary) html += `<span class="muted">${escapeHtml(r.scores.overall_summary)}</span>`;
    html += '</div>';
  });
  sessionSummaryEl.innerHTML = html;
}

btnModeGuided?.addEventListener('click', () => setMode('guided'));
btnModeFreeform?.addEventListener('click', () => setMode('freeform'));
btnLoadQuestions?.addEventListener('click', loadQuestions);
btnAddQuestion?.addEventListener('click', addCustomQuestion);
addQuestionInputEl?.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') addCustomQuestion();
});
btnStartGuided?.addEventListener('click', startGuidedInterview);
btnPlayQuestion?.addEventListener('click', () => speakQuestion(guidedState.questions[guidedState.currentQuestionIndex]));
btnStartAnswer?.addEventListener('click', onGuidedStartRecording);
btnStopAnswer?.addEventListener('click', onGuidedStopRecording);
btnTryAgain?.addEventListener('click', onTryAgain);
btnNextQuestion?.addEventListener('click', onNextQuestion);

setMode('guided');
checkSetup();
if (window.speechSynthesis) {
  speechSynthesis.onvoiceschanged = populateVoiceSelect;
  populateVoiceSelect();
}
