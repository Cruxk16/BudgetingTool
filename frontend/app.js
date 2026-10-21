const $ = (s, el = document) => el.querySelector(s);
const main = $('#main');
const money = (n, d = 0) => (n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const monthName = k => new Date(k + '-15').toLocaleString(undefined, { month: 'short', year: '2-digit' });
const dateName = iso => iso ? new Date(iso + 'T12:00:00').toLocaleString(undefined, { month: 'short', year: 'numeric' }) : '—';
let charts = [];

async function api(path, method = 'GET', body) {
  const res = await fetch('/api' + path, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    toast(msg);
    throw new Error(msg);
  }
  return res.json();
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.style.display = 'none'), 3500);
}
function chart(id, config) {
  const c = new Chart($('#' + id), { ...config, options: { responsive: true, maintainAspectRatio: false, ...config.options } });
  charts.push(c);
}
const PALETTE = ['#2f6fed', '#1a9b5f', '#d98a00', '#8e5bd6', '#d6403a', '#13a8b8', '#e0679a', '#7d8aa3', '#5a9a2c', '#b5651d'];

function bar(pct, status, pace) {
  const cls = status === 'over' ? 'bad' : status === 'over_pace' ? 'warn' : '';
  return `<div class="bar ${cls}"><i style="width:${Math.min(pct * 100, 100)}%"></i>${pace != null ? `<span class="pace" style="left:${pace * 100}%"></span>` : ''}</div>`;
}

// ---------- Pages ----------
const pages = {
  async '/'() {
    const [prog, goals, txns] = await Promise.all([api('/progress'), api('/goals'), api('/transactions?transfers=hide&limit=8')]);
    if (!prog.items.length && !txns.length) return emptyState();
    const f = prog.free;
    const bad = prog.items.filter(i => i.status === 'over' || i.status === 'over_pace');
    main.innerHTML = `
      <h1>${monthName(prog.month)} at a glance</h1>
      <div class="stats card">
        <div class="stat"><div class="v">${money(prog.income)}</div><div class="l">Monthly income</div></div>
        <div class="stat"><div class="v">${money(prog.total_spent)}</div><div class="l">Spent so far of ${money(prog.total_budget)}</div></div>
        <div class="stat"><div class="v ${prog.surplus < 0 ? 'bad' : 'good'}">${money(prog.surplus)}</div><div class="l">Planned monthly surplus</div></div>
        <div class="stat"><div class="v ${bad.length ? 'warn' : 'good'}">${bad.length}</div><div class="l">Categories over pace</div></div>
      </div>
      <div class="card" style="margin-bottom:16px"><h2>Left for fun spending this month</h2>
        <div class="stats">
          <div class="stat"><div class="v ${f.left < 0 ? 'bad' : 'good'}">${money(f.left)}</div><div class="l">after bills still to come and what you've spent</div></div>
          <div class="stat"><div class="v">${f.left > 0 ? money(f.per_day) : '$0'}</div><div class="l">per day for the next ${f.days_left} days</div></div>
          <div class="stat"><div class="v">${money(f.unplanned_spent)}</div><div class="l">already spent outside the plan</div></div>
          <div class="stat"><div class="v">${f.typical_months ? money(f.typical_total) : '—'}</div><div class="l">you usually spend outside the plan${f.typical_months ? ` (${f.typical_months}-mo avg)` : ''}</div></div>
        </div>
        ${f.categories.length ? `<table><tr><th>Category</th><th class="n">This month</th><th class="n">Usual / month</th></tr>${f.categories.map(c =>
          `<tr><td>${esc(c.category)}</td><td class="n">${money(c.this_month)}</td><td class="n muted">${c.typical != null ? money(c.typical) : '—'}</td></tr>`).join('')}</table>` : ''}
        <p class="muted" style="font-size:12px">Income − every planned line (unpaid bills held back; overspent lines counted at what you actually spent) − spending in categories with no budget.</p>
      </div>
      <div class="grid">
        <div class="card"><h2>Budget vs. actual</h2>
          ${prog.items.map(i => `<div class="prog"><div class="top"><span>${esc(i.category)}</span>
            <span class="muted">${money(i.spent)} / ${money(i.budget)} ${statusPill(i.status)}</span></div>
            ${i.budget ? bar(i.spent / i.budget, i.status, i.kind === 'variable' ? prog.day_fraction : null) : ''}</div>`).join('') || '<p class="muted">No budget yet — generate one on the Budget page.</p>'}
          <p class="muted" style="font-size:12px">Dark tick = where you'd be if spending were even through the month.</p>
        </div>
        <div>
          <div class="card" style="margin-bottom:16px"><h2>Goals</h2>
            ${goals.goals.map(g => `<div class="prog"><div class="top"><span>${esc(g.name)}</span><span class="muted">${money(g.saved)} / ${money(g.price)}</span></div>
              ${bar(g.progress)}<div class="muted" style="font-size:12px">${g.months_to_goal != null ? 'Done ~' + dateName(g.completion_date) : 'No contribution yet'}</div></div>`).join('') || '<p class="muted">No goals yet.</p>'}
          </div>
          <div class="card"><h2>Recent transactions</h2>${txnTable(txns.slice(0, 8), true)}</div>
        </div>
      </div>`;
  },

  async '/transactions'() {
    const [cats, accounts] = await Promise.all([api('/categories'), api('/accounts')]);
    main.innerHTML = `<h1>Transactions</h1>
      <div class="row">
        <input id="q" placeholder="Search merchant…">
        <select id="cat"><option value="">All categories</option>${[...cats, 'Card Payment', 'Transfer', 'Income'].map(c => `<option>${esc(c)}</option>`).join('')}</select>
        <select id="acct"><option value="0">All accounts</option>${accounts.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select>
        <select id="range"><option value="">Any date</option><option value="this">This month</option><option value="last">Last month</option><option value="3">Last 3 months</option><option value="custom">Custom…</option></select>
        <input id="d1" type="date" title="From"><input id="d2" type="date" title="To">
        <select id="tr"><option value="all">Incl. transfers</option><option value="hide">Hide transfers</option><option value="only">Only transfers</option></select>
      </div>
      <div class="card" id="list"></div>`;
    const load = async () => {
      const p = new URLSearchParams({ q: $('#q').value, category: $('#cat').value, account_id: $('#acct').value, transfers: $('#tr').value, limit: 500 });
      if ($('#d1').value) p.set('start', $('#d1').value);
      if ($('#d2').value) p.set('end', $('#d2').value);
      const rows = await api('/transactions?' + p);
      $('#list').innerHTML = txnTable(rows, false, cats);
      $('#list').querySelectorAll('select[data-id]').forEach(s => s.onchange = async () => {
        const apply = confirm('Apply this category to all transactions from this merchant?');
        const v = s.value;
        await api(`/transactions/${s.dataset.id}`, 'PATCH', v === '__transfer' ? { is_transfer: true, category: 'Transfer', apply_to_merchant: apply }
          : { category: v, is_transfer: v === 'Card Payment' || v === 'Transfer', apply_to_merchant: apply });
        load();
      });
    };
    ['q', 'cat', 'acct', 'tr', 'd1', 'd2'].forEach(id => $('#' + id).oninput = debounce(() => { if (id.startsWith('d')) $('#range').value = 'custom'; load(); }, 200));
    $('#range').onchange = () => {
      const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10), now = new Date(), v = $('#range').value;
      if (v === 'custom') return;
      const from = { this: new Date(now.getFullYear(), now.getMonth(), 1), last: new Date(now.getFullYear(), now.getMonth() - 1, 1), 3: new Date(now.getFullYear(), now.getMonth() - 2, 1) }[v];
      $('#d1').value = from ? iso(from) : '';
      $('#d2').value = v === 'last' ? iso(new Date(now.getFullYear(), now.getMonth(), 0)) : '';
      load();
    };
    load();
  },

  async '/budget'() {
    const b = await api('/budget');
    const [income, cats] = [b.income, await api('/categories')];
    const sect = (kind, title) => {
      const ls = b.lines.filter(l => l.kind === kind);
      return `<div class="card" style="margin-bottom:16px"><h2>${title} <span class="muted">${money(b.totals[kind])}/mo</span></h2>
        <table>${ls.map(l => `<tr><td>${esc(l.category)} ${l.flag ? `<span class="pill warn">${l.flag}</span>` : ''}<div class="muted" style="font-size:12px">${esc(l.detail || '')}</div></td>
          <td class="n muted" style="font-size:12px">${l.typical != null ? `you typically spend ${money(l.typical)}` : ''}</td>
          <td class="n">${b.saved ? `<input type="number" step="1" value="${l.amount}" data-line="${l.id}" style="width:100px;text-align:right">
          <button class="link" data-del="${l.id}">✕</button>` : money(l.amount)}</td></tr>`).join('') || '<tr><td class="muted">None detected</td></tr>'}</table></div>`;
    };
    main.innerHTML = `<h1>Budget — ${monthName(b.month)}</h1>
      <p class="muted">${b.saved ? 'This is your plan. Each new month starts as a copy of your baseline; “you typically spend” comes from your imported statements.' : 'No plan saved yet — this is a preview built from your spending history.'}</p>
      ${b.n_months ? `<p class="muted">Based on ${b.n_months} month${b.n_months > 1 ? 's' : ''} of data${b.n_months < 3 ? ' — fewer than 3, so treat this as rough' : ''}.</p>` : ''}
      <div class="row"><button class="primary" id="gen">${b.saved ? 'Replace with suggestion from my spending' : 'Save this budget'}</button>
        ${b.saved ? '<button id="tobase">Save as baseline</button><button id="reset">Reset to baseline</button>' : ''}
        ${b.saved ? `<select id="newcat">${cats.map(c => `<option>${esc(c)}</option>`).join('')}</select><button id="addline">Add line</button>` : ''}</div>
      <div class="card stats">
        <div class="stat"><div class="v">${money(income.monthly)}</div><div class="l">Income / mo ${income.overridden ? '(edited)' : '(detected)'}
          <button class="link" id="editinc">edit</button></div></div>
        <div class="stat"><div class="v">${money(b.totals.fixed)}</div><div class="l">Fixed</div></div>
        <div class="stat"><div class="v">${money(b.totals.sinking)}</div><div class="l">Sinking funds</div></div>
        ${b.totals.payroll ? `<div class="stat"><div class="v">${money(b.totals.payroll)}</div><div class="l">Via paycheck</div></div>` : ''}
        <div class="stat"><div class="v">${money(b.totals.variable)}</div><div class="l">Variable</div></div>
        <div class="stat"><div class="v ${b.totals.surplus < 0 ? 'bad' : 'good'}">${money(b.totals.surplus)}</div><div class="l">Surplus for goals</div></div>
      </div>
      ${income.sources.length ? `<div class="card" style="margin-bottom:16px"><h2>Detected income</h2><table>${income.sources.map(s =>
        `<tr><td>${esc(s.name)}</td><td class="muted">${money(s.amount, 2)} ${s.frequency}</td><td class="n">${money(s.monthly)}/mo</td></tr>`).join('')}</table></div>` : ''}
      ${b.suggestions.length ? `<div class="banner"><strong>You're ${money(-b.totals.surplus)} over.</strong> Possible cuts:<ul>${b.suggestions.map(s =>
        `<li>${esc(s.category)}: trim ${money(s.cut)} (from ${money(s.from)})</li>`).join('')}</ul></div>` : ''}
      ${sect('fixed', 'Fixed expenses')}${sect('payroll', 'Deducted from paycheck (not on statements)')}${sect('sinking', 'Sinking funds (irregular costs ÷ 12)')}${sect('variable', 'Variable spending')}`;
    $('#gen').onclick = async () => { if (b.saved && !confirm('Replace your plan with amounts based on your spending history?')) return; await api('/budget/generate', 'POST'); render(); };
    if (b.saved) {
      $('#tobase').onclick = async () => { await api('/budget/baseline', 'POST'); toast('Baseline updated'); };
      $('#reset').onclick = async () => { if (confirm('Discard this month\'s edits and go back to the baseline?')) { await api('/budget/reset', 'POST'); render(); } };
    }
    $('#editinc').onclick = async () => {
      const v = prompt('Monthly income (blank to go back to detected):', income.monthly);
      if (v === null) return;
      await api('/income', 'PUT', { monthly: v.trim() === '' ? null : parseFloat(v) }); render();
    };
    if (b.saved) {
      main.querySelectorAll('input[data-line]').forEach(i => i.onchange = async () => { await api(`/budget/lines/${i.dataset.line}`, 'PUT', { amount: parseFloat(i.value) || 0 }); render(); });
      main.querySelectorAll('[data-del]').forEach(x => x.onclick = async () => { await api(`/budget/lines/${x.dataset.del}`, 'DELETE'); render(); });
      $('#addline').onclick = async () => {
        const amount = parseFloat(prompt('Monthly amount:')); if (!amount) return;
        await api('/budget/lines', 'POST', { category: $('#newcat').value, amount, kind: 'variable' }); render();
      };
    }
  },

  async '/insights'() {
    const d = await api('/insights');
    if (!d.overview.some(o => o.spending)) return emptyState();
    main.innerHTML = `<h1>Insights</h1>
      <div class="grid"><div class="card" style="grid-column:1/-1"><h2>Income vs. spending</h2><div class="canvas-wrap"><canvas id="c1"></canvas></div></div></div>
      <div class="grid">
        <div class="card"><h2>${monthName(d.month)} by category</h2><div class="canvas-wrap"><canvas id="c2"></canvas></div></div>
        <div class="card"><h2>By card (${monthName(d.month)})</h2>${d.cards.map(c => `<div class="prog"><div class="top"><strong>${esc(c.card)}</strong><span>${money(c.total)}</span></div>
          <div class="muted" style="font-size:12px">${Object.entries(c.categories).slice(0, 4).map(([k, v]) => `${esc(k)} ${money(v)}`).join(' · ')}</div></div>`).join('') || '<p class="muted">No card spending.</p>'}</div>
      </div>
      <div class="grid">
        <div class="card"><h2>Top merchants — spend (3 mo)</h2><table>${d.merchants.by_spend.map(m => `<tr><td>${esc(m.merchant)}</td><td class="muted n">${m.visits}×</td><td class="n">${money(m.total)}</td></tr>`).join('')}</table></div>
        <div class="card"><h2>Top merchants — visits (3 mo)</h2><table>${d.merchants.by_visits.map(m => `<tr><td>${esc(m.merchant)}</td><td class="n">${m.visits}×</td><td class="muted n">${money(m.total)}</td></tr>`).join('')}</table></div>
      </div>
      <div class="grid">
        <div class="card"><h2>Month over month</h2><table>${d.mom.slice(0, 8).map(m => `<tr><td>${esc(m.category)}</td><td class="muted n">${money(m.previous)} → ${money(m.current)}</td>
          <td class="n ${m.change > 0 ? 'bad' : 'good'}">${m.change > 0 ? '+' : ''}${money(m.change)}</td></tr>`).join('')}</table></div>
        <div class="card"><h2>Subscriptions <span class="muted">${money(d.subscriptions.monthly_total, 2)}/mo · ${money(d.subscriptions.yearly_total)}/yr</span></h2>
          <table>${d.subscriptions.items.map(s => `<tr><td>${esc(s.name)}</td><td class="n">${money(s.monthly, 2)}/mo</td><td class="muted n">${money(s.yearly)}/yr</td></tr>`).join('') || '<tr><td class="muted">None found</td></tr>'}</table></div>
      </div>
      <div class="card"><h2>Unusual charges (last 60 days)</h2><table>${d.unusual.map(u => `<tr><td>${u.date}</td><td>${esc(u.merchant)}</td><td class="muted">usually ${money(u.usual, 2)}</td><td class="n bad">${money(u.amount, 2)}</td></tr>`).join('') || '<tr><td class="muted">Nothing out of the ordinary</td></tr>'}</table></div>`;
    chart('c1', { type: 'bar', data: { labels: d.overview.map(o => monthName(o.month)), datasets: [
      { label: 'Income', data: d.overview.map(o => o.income), backgroundColor: '#1a9b5f' },
      { label: 'Spending', data: d.overview.map(o => o.spending), backgroundColor: '#2f6fed' },
      { label: 'Net', type: 'line', data: d.overview.map(o => o.net), borderColor: '#d98a00', tension: .3 }] } });
    chart('c2', { type: 'doughnut', data: { labels: d.categories.map(c => c.category), datasets: [{ data: d.categories.map(c => c.amount), backgroundColor: PALETTE }] },
      options: { plugins: { legend: { position: 'right' } } } });
  },

  async '/goals'() {
    const { surplus, goals } = await api('/goals');
    main.innerHTML = `<h1>Savings goals</h1>
      <p class="muted">Monthly surplus available to allocate: <strong class="${surplus < 0 ? 'bad' : 'good'}">${money(surplus)}</strong>. Goals are funded in priority order (1 = first).</p>
      <div class="card" style="margin-bottom:16px"><h2>Add a goal</h2>
        <div class="row"><input id="gn" placeholder="What are you saving for?"><input id="gp" type="number" placeholder="Price" style="width:110px">
          <input id="gs" type="number" placeholder="Saved so far" style="width:130px"><input id="gm" type="number" placeholder="$/month (optional)" style="width:150px">
          <input id="gd" type="date" title="Target date (optional)"><input id="gpr" type="number" value="1" min="1" style="width:70px" title="Priority"><button class="primary" id="gadd">Add</button></div></div>
      ${goals.map(goalCard).join('') || '<p class="muted">No goals yet.</p>'}`;
    $('#gadd').onclick = async () => {
      const price = parseFloat($('#gp').value); if (!$('#gn').value || !price) return toast('Name and price are required');
      await api('/goals', 'POST', { name: $('#gn').value, price, saved_so_far: parseFloat($('#gs').value) || 0,
        monthly_contribution: parseFloat($('#gm').value) || null, target_date: $('#gd').value || null, priority: parseInt($('#gpr').value) || 1 });
      render();
    };
    main.querySelectorAll('[data-contrib]').forEach(b => b.onclick = async () => {
      const amount = parseFloat(prompt('Amount you put toward this goal:')); if (!amount) return;
      await api(`/goals/${b.dataset.contrib}/contributions`, 'POST', { amount }); render();
    });
    main.querySelectorAll('[data-delgoal]').forEach(b => b.onclick = async () => { if (confirm('Delete this goal?')) { await api(`/goals/${b.dataset.delgoal}`, 'DELETE'); render(); } });
    main.querySelectorAll('input[data-whatif]').forEach(s => s.oninput = () => {
      const g = goals.find(x => x.id == s.dataset.whatif), v = parseFloat(s.value);
      const m = Math.ceil(g.remaining / v), d = new Date(); d.setMonth(d.getMonth() + m);
      $(`#wi${g.id}`).textContent = `Save ${money(v)}/mo → done in ${m} month${m > 1 ? 's' : ''} (${d.toLocaleString(undefined, { month: 'short', year: 'numeric' })})`;
    });
  },

  async '/ask'() {
    const [st, mdl] = await Promise.all([api('/ai/status'), api('/ai/models')]);
    const chat = pages._chat ||= [];
    const suggestions = ['Where did most of my money go this month?', 'How much fun money do I have left, and what\'s my daily limit?',
      'Which categories am I overspending vs my plan?', 'Any subscriptions I should cancel?', 'Can I afford a $1,200 purchase this month?'];
    main.innerHTML = `<h1>Ask AI</h1>
      ${st.configured ? '' : `<div class="card" style="margin-bottom:16px"><h2>Add your Gemini API key</h2>
        <p class="muted">Get a free key at aistudio.google.com/apikey. It's stored only in <code>data/gemini.key</code> on this computer.</p>
        <div class="row"><input id="key" type="password" placeholder="Gemini API key" style="width:320px"><button class="primary" id="savekey">Save</button></div></div>`}
      <div class="row"><label class="muted" for="model">Model</label>
        <select id="model">${mdl.models.map(m => `<option ${m === mdl.current ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select>
        <span class="muted" style="font-size:12px">${mdl.from_google ? 'models available to your key' : 'built-in list — save your key to see the models your key can use'}</span></div>
      <p class="muted" style="font-size:13px">Privacy: nothing is sent until you ask. Then only the data needed to answer (e.g. category totals, or matching transactions with their descriptions and amounts) is sent to Google's Gemini.</p>
      <div class="chips">${suggestions.map(q => `<button data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>
      <div class="chat" id="chat"></div>
      <div class="row"><input id="q" placeholder="Ask about your spending…" style="flex:1;min-width:240px"><button class="primary" id="send">Ask</button>
        ${chat.length ? '<button id="clear">Clear</button>' : ''}</div>`;
    const draw = () => {
      $('#chat').innerHTML = chat.map(m => `<div class="msg ${m.role === 'user' ? 'user' : 'ai'}${m.error ? ' err' : ''}">${m.role === 'user' ? esc(m.text) : md(m.text)}${m.tools?.length ? `<span class="tools">Looked at: ${[...new Set(m.tools)].map(t => esc(t.replace(/_/g, ' '))).join(', ')}</span>` : ''}</div>`).join('');
      window.scrollTo(0, document.body.scrollHeight);
    };
    const ask = async question => {
      if (!question.trim()) return;
      const history = chat.filter(m => !m.error).map(m => ({ role: m.role, text: m.text }));
      chat.push({ role: 'user', text: question }); draw(); $('#q').value = '';
      chat.push({ role: 'ai', text: 'Thinking…' }); draw();
      try {
        const r = await api('/ai/ask', 'POST', { question, history });
        chat[chat.length - 1] = { role: 'ai', text: r.answer, tools: r.tools_used };
      } catch (e) { chat[chat.length - 1] = { role: 'ai', text: '⚠ ' + e.message, error: true }; }
      draw();
    };
    $('#send').onclick = () => ask($('#q').value);
    $('#q').onkeydown = e => { if (e.key === 'Enter') ask($('#q').value); };
    main.querySelectorAll('[data-q]').forEach(b => b.onclick = () => ask(b.dataset.q));
    $('#model').onchange = async () => { await api('/ai/model', 'PUT', { model: $('#model').value }); toast('Model set to ' + $('#model').value); };
    if ($('#savekey')) $('#savekey').onclick = async () => { await api('/ai/key', 'PUT', { key: $('#key').value }); render(); };
    if ($('#clear')) $('#clear').onclick = () => { chat.length = 0; render(); };
    draw();
  },

  async '/settings'() {
    const [accounts, rules, cats] = await Promise.all([api('/accounts'), api('/rules'), api('/categories')]);
    const roles = { income_bills: 'Income & bills', spending: 'Spending (card)', savings: 'Savings', ignore: 'Ignore' };
    main.innerHTML = `<h1>Settings</h1>
      <div id="preview"></div>
      <div class="card" style="margin-bottom:16px"><h2>Add an account</h2>
        <p class="muted">Add each account once (e.g. Wells Fargo checking, Chase card, Capital One card), then upload its statements (PDF or CSV) below. Re-uploading overlapping date ranges is safe — duplicates are skipped.</p>
        <div class="row"><input id="ai" placeholder="Bank (e.g. Chase)"><input id="an" placeholder="Account name"><input id="am" placeholder="Last 4" style="width:90px" maxlength="4">
          <select id="at"><option value="checking">Checking</option><option value="credit">Credit card</option><option value="savings">Savings</option></select>
          <button class="primary" id="aadd">Add account</button></div>
        <button id="demo" class="link">or load demo data</button></div>
      ${accounts.map(a => `<div class="card" style="margin-bottom:16px"><div class="row" style="justify-content:space-between"><h2>${esc(a.institution)} — ${esc(a.name)} <span class="muted">${a.mask ? '••' + esc(a.mask) : ''}</span></h2>
        <span><select data-acct="${a.id}">${Object.entries(roles).map(([k, v]) => `<option value="${k}" ${a.role === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
        <button data-disc="${a.id}">Delete</button></span></div>
        <p class="muted">${a.transaction_count ? `${a.transaction_count} transactions, ${a.first_date} → ${a.last_date}` : 'No transactions yet.'}</p>
        <div class="row"><input type="file" accept=".pdf,.csv,.txt" data-file="${a.id}"><button class="primary" data-up="${a.id}">Upload statement</button></div></div>`).join('')}
      <div class="card"><h2>Category rules</h2><p class="muted">Rules always win over automatic categories. You can also create one from the Transactions page when recategorizing.</p>
        <table>${rules.map(r => `<tr><td>${r.match_type} “${esc(r.pattern)}”</td><td>${esc(r.category)}</td><td class="n"><button class="link" data-delrule="${r.id}">remove</button></td></tr>`).join('')}</table>
        <div class="row" style="margin-top:10px"><select id="rt"><option value="merchant">merchant is</option><option value="contains">name contains</option></select>
          <input id="rp" placeholder="e.g. shell"><select id="rc">${[...cats, 'Card Payment', 'Transfer'].map(c => `<option>${esc(c)}</option>`).join('')}</select><button id="radd">Add rule</button></div></div>`;
    $('#aadd').onclick = async () => {
      if (!$('#ai').value || !$('#an').value) return toast('Bank and account name are required');
      await api('/accounts', 'POST', { institution: $('#ai').value, name: $('#an').value, mask: $('#am').value || null, type: $('#at').value }); render();
    };
    $('#demo').onclick = async () => { if (confirm('Load 12 months of fake data?')) { const r = await api('/demo/seed', 'POST'); toast(`Loaded ${r.transactions} demo transactions`); render(); } };
    main.querySelectorAll('[data-up]').forEach(b => b.onclick = async () => {
      const f = main.querySelector(`input[data-file="${b.dataset.up}"]`).files[0];
      if (!f) return toast('Choose a statement file first');
      const fd = new FormData(); fd.append('file', f);
      toast('Reading statement…');
      const res = await fetch(`/api/accounts/${b.dataset.up}/import/preview`, { method: 'POST', body: fd });
      const j = await res.json();
      if (!res.ok) return toast(j.detail || 'Could not read that file');
      showPreview(+b.dataset.up, accounts.find(a => a.id == b.dataset.up), f.name, j.rows);
    });
    main.querySelectorAll('[data-disc]').forEach(b => b.onclick = async () => { if (confirm('Delete this account and all its transactions?')) { await api(`/accounts/${b.dataset.disc}`, 'DELETE'); render(); } });
    main.querySelectorAll('[data-acct]').forEach(s => s.onchange = async () => { await api(`/accounts/${s.dataset.acct}`, 'PATCH', { role: s.value }); toast('Role updated'); });
    main.querySelectorAll('[data-delrule]').forEach(b => b.onclick = async () => { await api(`/rules/${b.dataset.delrule}`, 'DELETE'); render(); });
    $('#radd').onclick = async () => {
      if (!$('#rp').value) return; const c = $('#rc').value;
      await api('/rules', 'POST', { match_type: $('#rt').value, pattern: $('#rp').value, category: c, is_transfer: c === 'Transfer' || c === 'Card Payment' }); render();
    };
  },
};

// ---------- Import preview ----------
function showPreview(accountId, account, filename, rows) {
  const box = $('#preview');
  const draw = () => {
    const out = rows.filter(r => r.amount > 0).reduce((s, r) => s + r.amount, 0);
    const inn = rows.filter(r => r.amount < 0).reduce((s, r) => s - r.amount, 0);
    const dates = rows.map(r => r.date).sort();
    box.innerHTML = `<div class="card" style="margin-bottom:16px;border-color:var(--accent)">
      <h2>Check before importing — ${esc(filename)}</h2>
      <p>${rows.length} transactions for <strong>${esc(account.name)}</strong>, ${dates[0]} → ${dates[dates.length - 1]}.
        Money out <strong>${money(out, 2)}</strong>, money in <strong>${money(inn, 2)}</strong>.
        ${account.type === 'credit' ? 'On a card, purchases should be the big "money out" number and payments the "money in".' : 'On checking, compare these totals to the statement summary.'}</p>
      <p class="muted">If the totals look backwards or don't match your statement, flip the signs. Bad rows can be removed with ✕.</p>
      <div class="row"><button class="primary" id="pv-ok">Import ${rows.length}</button><button id="pv-flip">Flip signs</button><button id="pv-cancel">Cancel</button></div>
      <div style="max-height:320px;overflow:auto"><table>${rows.map((r, i) => `<tr><td class="muted">${r.date}</td><td>${esc(r.name)}</td>
        <td class="n ${r.amount < 0 ? 'good' : ''}">${money(-r.amount, 2)}</td><td><button class="link" data-rm="${i}">✕</button></td></tr>`).join('')}</table></div></div>`;
    $('#pv-flip').onclick = () => { rows.forEach(r => (r.amount = -r.amount)); draw(); };
    $('#pv-cancel').onclick = () => (box.innerHTML = '');
    box.querySelectorAll('[data-rm]').forEach(x => x.onclick = () => { rows.splice(+x.dataset.rm, 1); draw(); });
    $('#pv-ok').onclick = async () => {
      const j = await api(`/accounts/${accountId}/import`, 'POST', { rows });
      toast(`Added ${j.added} transactions (${j.skipped_duplicates} duplicates skipped)`); render();
    };
    box.scrollIntoView({ behavior: 'smooth' });
  };
  draw();
}

// ---------- Shared pieces ----------
function statusPill(s) {
  return { over: '<span class="pill bad">over</span>', over_pace: '<span class="pill warn">over pace</span>', on_pace: '<span class="pill good">on pace</span>',
    paid: '<span class="pill good">paid</span>', payroll: '<span class="pill">via paycheck</span>', unplanned: '<span class="pill">no budget</span>', upcoming: '<span class="pill">upcoming</span>' }[s] || '';
}
function txnTable(rows, compact, cats = []) {
  if (!rows.length) return '<p class="muted">No transactions.</p>';
  return `<table><tr><th>Date</th><th>Merchant</th>${compact ? '' : '<th>Account</th>'}<th>Category</th><th class="n">Amount</th></tr>${rows.map(t => `<tr>
    <td class="muted">${t.date}</td><td>${esc(t.merchant_name || t.name)}</td>
    ${compact ? '' : `<td class="muted">${esc(t.account)} ••${esc(t.mask || '')}</td>`}
    <td>${compact ? esc(t.category) : `<select data-id="${t.id}">${[...cats, 'Income', 'Card Payment', 'Transfer'].map(c => `<option ${c === t.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}<option value="__transfer">Mark as account transfer</option></select>`}</td>
    <td class="n ${t.is_transfer ? 'muted' : t.amount < 0 ? 'good' : ''}">${money(-t.amount, 2)}</td></tr>`).join('')}</table>`;
}
function goalCard(g) {
  const base = g.months_to_goal;
  return `<div class="card" style="margin-bottom:16px">
    <div class="row" style="justify-content:space-between"><h2>${esc(g.name)} <span class="pill">priority ${g.priority}</span></h2>
      <span><button data-contrib="${g.id}">+ Log contribution</button><button class="link" data-delgoal="${g.id}">delete</button></span></div>
    <div class="top" style="display:flex;justify-content:space-between"><span>${money(g.saved)} of ${money(g.price)}</span><span class="muted">${money(g.remaining)} to go</span></div>${bar(g.progress)}
    <p>${g.remaining === 0 ? '<strong class="good">Goal reached 🎉</strong>' : base != null
      ? `At <strong>${money(g.allocated_monthly)}/mo</strong> you'll finish in <strong>${base} month${base > 1 ? 's' : ''}</strong> (${dateName(g.completion_date)}).`
      : 'No surplus is available for this goal right now.'}
      ${g.target_date ? (g.feasible ? `<span class="pill good">fits by ${dateName(g.target_date)}</span>` : `<span class="pill bad">needs ${money(g.required_monthly)}/mo, short ${money(g.shortfall)}</span>`) : ''}</p>
    ${g.faster.length ? `<div><strong>Get there faster</strong><ul>${g.faster.map(f => `<li>Cut ${esc(f.category)} by ${money(f.cut)}/mo → ${dateName(f.new_completion)}${f.months_saved ? ` (${f.months_saved} month${f.months_saved > 1 ? 's' : ''} sooner)` : ''}</li>`).join('')}</ul></div>` : ''}
    ${g.remaining > 0 ? `<div class="row"><span class="muted">What if I save</span><input type="range" min="25" max="${Math.max(2000, Math.ceil(g.remaining))}" step="25" value="${Math.max(25, Math.round(g.allocated_monthly / 25) * 25)}" data-whatif="${g.id}">
      <span id="wi${g.id}" class="muted"></span></div>` : ''}</div>`;
}
function emptyState() {
  main.innerHTML = `<div class="card"><h1>Welcome</h1><p>No data yet. Add your accounts and upload bank CSVs in <a href="#/settings">Settings</a>, or load demo data there to explore the app first.</p></div>`;
}
// Minimal markdown: **bold**, "- " bullets, line breaks — on already-escaped text.
const md = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/^[-*] /gm, '• ');
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function render() {
  charts.forEach(c => c.destroy()); charts = [];
  const path = location.hash.slice(1) || '/';
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + path));
  try { await (pages[path] || pages['/'])(); } catch (e) { console.error(e); }
  document.querySelectorAll('input[data-whatif]').forEach(s => s.dispatchEvent(new Event('input')));
}
window.addEventListener('hashchange', render);
render();
