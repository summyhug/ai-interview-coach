const btnStart = document.getElementById('btnStart');
const btnStop = document.getElementById('btnStop');
const recIndicator = document.getElementById('recIndicator');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const includeRewritesEl = document.getElementById('includeRewrites');
const setupBanner = document.getElementById('setupBanner');

let mediaRecorder = null;
let audioChunks = [];

async function checkSetup() {
  try {
    const res = await fetch('/api/check');
    const data = await res.json();
    const missing = [];
    if (!data.ffmpeg) missing.push('ffmpeg (brew install ffmpeg)');
    if (!data.ollama) missing.push(`Ollama + model (ollama serve && ollama pull ${data.model || 'llama3.2'})`);
    if (missing.length) {
      setupBanner.textContent = 'Before recording: ' + missing.join(', ');
      setupBanner.className = 'setup-banner warn';
      setupBanner.style.display = 'block';
    } else {
      setupBanner.textContent = 'Ready: ffmpeg and Ollama detected.';
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

async function uploadAndAnalyze(blob) {
  setStatus('Transcribing and scoring...', 'loading');
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');
  const includeRewrites = includeRewritesEl.checked;
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
    renderResults(data);
    clearStatus();
  } catch (err) {
    setStatus('Error: ' + err.message, 'error');
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

checkSetup();
