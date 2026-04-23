// Time Off — prototype logic. All state lives in the browser.

const STATE = {
  windows: [],
  selectedWindow: null,
  chat: [],
  turns: 0,
  profile: loadProfile(),
  history: loadHistory(),
};

// ---------- tabs ----------
const tabs = document.querySelectorAll('.tab-btn');
const panels = document.querySelectorAll('[data-panel]');
tabs.forEach((t) =>
  t.addEventListener('click', () => {
    const name = t.dataset.tab;
    panels.forEach((p) => p.classList.toggle('hidden', p.dataset.panel !== name));
    tabs.forEach((b) => b.classList.toggle('bg-ink', false));
    tabs.forEach((b) => b.classList.toggle('text-white', false));
    t.classList.add('bg-ink', 'text-white');
    if (name === 'settings') renderHistory();
  })
);
// default tab
document.querySelector('[data-tab="planner"]').click();

// prefill earliest start = today
document.getElementById('earliest').valueAsDate = new Date();

// ---------- planner ----------
const US_HOLIDAYS_2026 = [
  { name: 'Memorial Day',   date: '2026-05-25' },
  { name: 'Juneteenth',     date: '2026-06-19' },
  { name: 'Independence Day', date: '2026-07-03' }, // observed
  { name: 'Labor Day',      date: '2026-09-07' },
  { name: 'Thanksgiving',   date: '2026-11-26' },
  { name: 'Christmas',      date: '2026-12-25' },
];

function parseDate(s) { return new Date(s + 'T00:00:00'); }
function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
function fmtDate(d) {
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}
function daysBetween(a, b) { return Math.round((b - a) / 86400000); }

// count weekdays in [start, end)
function workdaysInRange(start, len) {
  let n = 0;
  for (let i = 0; i < len; i++) {
    const d = addDays(start, i).getDay();
    if (d !== 0 && d !== 6) n++;
  }
  return n;
}

// synthetic flight-price model: cheaper midweek, dearer near holidays, dearer Fri/Sat departures
function priceIndex(startDate) {
  let base = 100;
  const dow = startDate.getDay();
  if (dow === 2 || dow === 3) base -= 18;         // Tue/Wed cheap
  if (dow === 5 || dow === 6) base += 22;         // Fri/Sat pricey
  // holiday proximity — within 5 days of a US holiday = price surge
  for (const h of US_HOLIDAYS_2026) {
    const diff = Math.abs(daysBetween(parseDate(h.date), startDate));
    if (diff <= 3) base += 35;
    else if (diff <= 7) base += 12;
  }
  // seasonal bump for summer (Jun-Aug)
  const m = startDate.getMonth();
  if (m >= 5 && m <= 7) base += 10;
  return Math.max(40, base);
}

// did this window bridge a holiday (get a freebie day)?
function bridgedHoliday(startDate, len) {
  for (const h of US_HOLIDAYS_2026) {
    const hd = parseDate(h.date);
    const inside = hd >= startDate && hd < addDays(startDate, len);
    const day = hd.getDay();
    if (inside && day >= 1 && day <= 5) return h.name;
  }
  return null;
}

function scoreWindow({ start, len, ptoLeft, coverage, workload }) {
  const workdays = workdaysInRange(start, len);
  const ptoAfter = ptoLeft - workdays;
  const price = priceIndex(start);
  const bridge = bridgedHoliday(start, len);

  let score = 100;
  const reasons = [];

  // PTO math
  if (ptoAfter < 0) { score -= 60; reasons.push(`Short ${Math.abs(ptoAfter)} PTO days`); }
  else if (ptoAfter < 3) { score -= 15; reasons.push(`Leaves only ${ptoAfter} PTO for the rest of year`); }
  else { reasons.push(`${workdays} workdays used, ${ptoAfter} PTO left`); }

  // price
  if (price <= 85) { score += 12; reasons.push('Cheap flight window'); }
  else if (price >= 130) { score -= 18; reasons.push('Flights surge in this window'); }
  else reasons.push('Average flight prices');

  // holiday bridge = free day
  if (bridge) { score += 10; reasons.push(`Bridges ${bridge} — one PTO day saved`); }

  // coverage
  if (coverage === 'strong') { score += 10; reasons.push('Strong team coverage'); }
  if (coverage === 'thin') { score -= 20; reasons.push('Coverage gap — needs a handoff plan'); }

  // workload
  if (workload === 'calm') { score += 8; reasons.push('Calm workload window'); }
  if (workload === 'crunch') { score -= 25; reasons.push('Overlaps a crunch period'); }

  // saturdays/sundays spent = less PTO burn = small bonus
  const weekendDays = len - workdays;
  if (weekendDays >= 2) reasons.push(`${weekendDays} weekend days inside the trip`);

  score = Math.max(5, Math.min(99, Math.round(score)));
  return { start, len, workdays, ptoAfter, price, bridge, score, reasons };
}

function runPlanner() {
  const length = +document.getElementById('length').value;
  const pto = +document.getElementById('pto').value;
  const earliest = parseDate(document.getElementById('earliest').value);
  const coverage = document.getElementById('coverage').value;
  const workload = document.getElementById('workload').value;

  // generate candidates — every 4 days for ~90 days
  const candidates = [];
  for (let offset = 0; offset <= 90; offset += 4) {
    const start = addDays(earliest, offset);
    candidates.push(scoreWindow({ start, len: length, ptoLeft: pto, coverage, workload }));
  }
  candidates.sort((a, b) => b.score - a.score);

  // dedupe within 7 days of each other, keep top 3
  const picks = [];
  for (const c of candidates) {
    if (picks.every((p) => Math.abs(daysBetween(p.start, c.start)) > 7)) picks.push(c);
    if (picks.length === 3) break;
  }

  STATE.windows = picks;
  STATE.selectedWindow = picks[0] || null;
  renderPlannerResults();
  populateWindowPicker();
}

function renderPlannerResults() {
  const root = document.getElementById('plannerResults');
  if (!STATE.windows.length) {
    root.innerHTML = `<div class="card p-5 text-sm text-slate-500">No windows yet.</div>`;
    return;
  }
  const labels = ['Best overall', 'Cheaper alternative', 'If timing slips'];
  root.innerHTML = STATE.windows
    .map((w, i) => {
      const end = addDays(w.start, w.len - 1);
      const price = w.price <= 85 ? 'Low' : w.price >= 130 ? 'High' : 'Medium';
      return `
        <div class="card p-5">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="chip mb-2 inline-block">${labels[i] || 'Option'}</div>
              <div class="font-display text-xl">${fmtDate(w.start)} &rarr; ${fmtDate(end)}</div>
              <div class="text-sm text-slate-500 mt-1">${w.workdays} workdays &middot; flights: ${price} &middot; ${w.ptoAfter} PTO left after</div>
            </div>
            <div class="text-right">
              <div class="text-3xl font-display text-pine">${w.score}</div>
              <div class="text-xs text-slate-400">score</div>
            </div>
          </div>
          <div class="mt-3 h-1.5 rounded bg-slate-100 overflow-hidden">
            <div class="h-full bg-pine" style="width:${w.score}%"></div>
          </div>
          <ul class="mt-3 text-sm text-slate-600 list-disc pl-5 space-y-1">
            ${w.reasons.map((r) => `<li>${r}</li>`).join('')}
          </ul>
          <div class="mt-4 flex gap-2">
            <button class="btn-ghost pick" data-idx="${i}">Use this window</button>
          </div>
        </div>`;
    })
    .join('');
  root.querySelectorAll('.pick').forEach((b) =>
    b.addEventListener('click', () => {
      STATE.selectedWindow = STATE.windows[+b.dataset.idx];
      document.querySelector('[data-tab="coach"]').click();
      document.getElementById('pickWindow').value = b.dataset.idx;
    })
  );
}

function populateWindowPicker() {
  const sel = document.getElementById('pickWindow');
  sel.innerHTML = STATE.windows
    .map((w, i) => {
      const end = addDays(w.start, w.len - 1);
      return `<option value="${i}">${fmtDate(w.start)} → ${fmtDate(end)} (score ${w.score})</option>`;
    })
    .join('');
  sel.value = 0;
}

document.getElementById('runPlanner').addEventListener('click', runPlanner);

// ---------- coach: draft ----------
function draftMessage() {
  const idx = +document.getElementById('pickWindow').value;
  const win = STATE.windows[idx];
  if (!win) { alert('Run the planner and pick a window first.'); return; }
  const reason = document.getElementById('reason').value.trim() || 'a planned break';
  const channel = document.getElementById('channel').value;
  const dest = document.getElementById('destination').value || 'my trip';
  const boss = STATE.profile.boss || 'there';
  const me = STATE.profile.name || '';
  const end = addDays(win.start, win.len - 1);
  const range = `${fmtDate(win.start)} – ${fmtDate(end)}`;

  let body;
  if (channel === 'slack') {
    body = `Hey ${boss} — wanted to flag early: hoping to take PTO ${range} for ${dest} (${reason}).
That's ${win.workdays} workdays; I'd leave ${win.ptoAfter} days in the bank.
Coverage plan: I'll write a handoff doc the week before and line up backup for anything owner-critical. Happy to push by a week if it clashes with something on your radar — wdyt?`;
  } else if (channel === 'email') {
    body = `Subject: PTO request — ${range}

Hi ${boss},

I'd like to request PTO from ${range} (${win.workdays} workdays, ${dest}). This is ${reason}, and I've been tracking ahead on my current load.

Plan while I'm out:
  • Handoff doc delivered by EOD the Friday before.
  • ${STATE.profile.team || 'The team'} rotating on-call as usual; I'll name a primary backup for escalations.
  • No launches scheduled in this window.
  • I'll be fully offline, back on ${fmtDate(addDays(end, 1))}.

After this trip I'll have ${win.ptoAfter} PTO days remaining for the year. Let me know if this works or if you'd prefer I shift by a week.

Thanks,
${me}`;
  } else {
    body = `1:1 talking points — PTO ask

• Ask: ${range} (${win.workdays} workdays) for ${dest}.
• Why now: ${reason}; window scored ${win.score} on price + coverage.
• Coverage: handoff doc, named backup, no launches in window.
• PTO balance after: ${win.ptoAfter} days.
• Flex: can push by a week; would rather not push by a month (prices jump).
• Ask for: verbal OK today, I'll file formally after.`;
  }
  document.getElementById('draftOut').textContent = body;
}

document.getElementById('draftBtn').addEventListener('click', draftMessage);
document.getElementById('copyDraft').addEventListener('click', async () => {
  const text = document.getElementById('draftOut').innerText;
  try { await navigator.clipboard.writeText(text); flash('Copied'); } catch { flash('Copy failed'); }
});

// ---------- coach: rehearsal ----------
const BOSS_LINES = {
  skeptic: {
    open: (w) => `Thanks for flagging. ${w.workdays} workdays is a lot — who's covering the ${randomPick(['Q2 review', 'onboarding ramp', 'vendor sync', 'incident rotation'])} while you're out?`,
    turns: [
      (w) => `OK — and what happens if ${randomPick(['the migration slips', 'the client escalates', 'the on-call handoff breaks'])} in that week?`,
      (w) => `I hear you. Can you move it one week later? That would dodge ${randomPick(['the board read-out', 'the release freeze', 'the all-hands'])}.`,
      (w) => `Fine, but I want the handoff doc by the Friday before, and a named backup. Send that and we're good.`,
    ],
  },
  warm: {
    open: (w) => `Oh nice — ${randomPick(['you deserve it', 'good for you', 'that sounds lovely'])}. ${w.workdays} workdays, though — will we be OK on ${randomPick(['the launch', 'the weekly report', 'the client demo'])}?`,
    turns: [
      (w) => `Totally fair. Who's your backup so I know who to ping?`,
      (w) => `And you'll really be offline? No "just checking Slack" this time? :)`,
      (w) => `Alright — approved in principle. File it and I'll rubber-stamp.`,
    ],
  },
  blunt: {
    open: (w) => `${w.workdays} days. What's the coverage plan?`,
    turns: [
      (w) => `Risks while you're out — top two.`,
      (w) => `Can it move? Yes or no.`,
      (w) => `Fine. Handoff doc by Friday prior. Approved.`,
    ],
  },
};

function randomPick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function startRehearsal() {
  const idx = +document.getElementById('pickWindow').value;
  const win = STATE.windows[idx];
  if (!win) { alert('Run the planner and pick a window first.'); return; }
  const style = document.getElementById('bossStyle').value;
  STATE.chat = [];
  STATE.turns = 0;
  STATE.rehearsalStyle = style;
  STATE.rehearsalWindow = win;
  document.getElementById('debrief').classList.add('hidden');

  const opener = BOSS_LINES[style].open(win);
  pushBubble('boss', opener);
}

function pushBubble(role, text) {
  STATE.chat.push({ role, text });
  renderChat();
}

function renderChat() {
  const chat = document.getElementById('chat');
  chat.innerHTML = STATE.chat
    .map((m) => {
      if (m.role === 'user') {
        return `<div class="flex justify-end"><div class="bubble-user px-3 py-2 text-sm max-w-[85%]">${escapeHtml(m.text)}</div></div>`;
      }
      return `<div class="flex"><div class="bubble-boss px-3 py-2 text-sm max-w-[85%]">${escapeHtml(m.text)}</div></div>`;
    })
    .join('');
  chat.scrollTop = chat.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

function sendUserLine() {
  const inp = document.getElementById('userLine');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  pushBubble('user', text);

  const style = STATE.rehearsalStyle || 'warm';
  const win = STATE.rehearsalWindow;
  const next = BOSS_LINES[style].turns[STATE.turns];
  STATE.turns++;
  if (next) {
    setTimeout(() => pushBubble('boss', next(win)), 400);
  } else {
    setTimeout(() => {
      pushBubble('boss', `(end of rehearsal)`);
      debrief();
    }, 400);
  }
}

function debrief() {
  const lines = STATE.chat.filter((m) => m.role === 'user').map((m) => m.text);
  const covered = {
    coverage: /cover|backup|handoff|on.call/i.test(lines.join(' ')),
    risk: /risk|launch|deadline|freeze/i.test(lines.join(' ')),
    flex: /move|shift|flex|alternative|later|earlier/i.test(lines.join(' ')),
    offline: /offline|unreachable|out of office|ooo/i.test(lines.join(' ')),
  };
  const missed = Object.entries(covered).filter(([, v]) => !v).map(([k]) => k);
  const box = document.getElementById('debrief');
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="font-display text-base mb-1">Debrief</div>
    <div class="text-slate-600">You covered: ${Object.entries(covered).filter(([,v])=>v).map(([k])=>k).join(', ') || '—'}.</div>
    ${missed.length ? `<div class="text-coral mt-1">Didn't address: ${missed.join(', ')}. Worth prepping a line for each before the real 1:1.</div>` : `<div class="text-pine mt-1">Solid — you hit the four things bosses typically probe on.</div>`}
  `;
  saveToHistory({
    when: new Date().toISOString(),
    style: STATE.rehearsalStyle,
    score: Object.values(covered).filter(Boolean).length,
    window: STATE.rehearsalWindow ? `${fmtDate(STATE.rehearsalWindow.start)} +${STATE.rehearsalWindow.len}d` : '',
  });
}

document.getElementById('rehearseBtn').addEventListener('click', startRehearsal);
document.getElementById('sendLine').addEventListener('click', sendUserLine);
document.getElementById('userLine').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendUserLine();
});

// ---------- profile + history ----------
function loadProfile() {
  try { return JSON.parse(localStorage.getItem('timeoff.profile') || '{}'); } catch { return {}; }
}
function saveProfile() {
  STATE.profile = {
    name: document.getElementById('profName').value,
    boss: document.getElementById('profBoss').value,
    team: document.getElementById('profTeam').value,
  };
  localStorage.setItem('timeoff.profile', JSON.stringify(STATE.profile));
  flash('Saved');
}
function hydrateProfile() {
  document.getElementById('profName').value = STATE.profile.name || '';
  document.getElementById('profBoss').value = STATE.profile.boss || '';
  document.getElementById('profTeam').value = STATE.profile.team || '';
}
function loadHistory() {
  try { return JSON.parse(localStorage.getItem('timeoff.history') || '[]'); } catch { return []; }
}
function saveToHistory(entry) {
  STATE.history.unshift(entry);
  STATE.history = STATE.history.slice(0, 20);
  localStorage.setItem('timeoff.history', JSON.stringify(STATE.history));
}
function renderHistory() {
  const ul = document.getElementById('historyList');
  if (!STATE.history.length) { ul.innerHTML = `<li class="text-slate-400">Nothing yet. Run a rehearsal.</li>`; return; }
  ul.innerHTML = STATE.history.map((h) => `
    <li class="flex justify-between border-b border-slate-100 pb-2">
      <span>${new Date(h.when).toLocaleString()} · ${h.style} · ${h.window}</span>
      <span class="chip">${h.score}/4 covered</span>
    </li>
  `).join('');
}
document.getElementById('saveProf').addEventListener('click', saveProfile);
hydrateProfile();

// ---------- tiny toast ----------
function flash(msg) {
  const el = document.createElement('div');
  el.textContent = msg;
  el.className = 'fixed bottom-6 right-6 bg-ink text-white text-sm rounded-lg px-3 py-2 shadow-lg';
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1200);
}
