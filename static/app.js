async function fetchState() {
  const res = await fetch('/api/state');
  return res.json();
}

function fmtUsd(n) {
  if (n === null || n === undefined) return '—';
  return '$' + Number(n).toFixed(2);
}

function fmtEcon(val) {
  return val === 'insufficient_data' ? 'insufficient data' : (typeof val === 'number' ? '$' + val : val);
}

function fmtVal(v) {
  return v === 'NOT_AVAILABLE' ? 'NOT AVAILABLE' : v;
}

const HERO_LABELS = {
  RUNNING: 'RUNNING', WAITING_FOR_DATA: 'WAITING FOR DATA', EXECUTING: 'EXECUTING',
  FOUNDER_BLOCKED: 'FOUNDER BLOCKED', ERROR: 'ERROR', IDLE: 'IDLE',
};

function render(data) {
  if (!data.seeded) {
    document.querySelector('.main').innerHTML = '<section class="card"><p>Not seeded yet. Run: <code>python3 seed_from_ro.py</code></p></section>';
    return;
  }
  const v = data.venture;
  document.getElementById('phasePill').textContent = v.phase;

  // ---- 1. System status hero ----
  const sys = data.system_status || {};
  const heroEl = document.getElementById('heroState');
  heroEl.textContent = HERO_LABELS[sys.state] || sys.state || '—';
  heroEl.className = 'hero-state hero-' + (sys.state || '').toLowerCase();
  document.getElementById('heroReason').textContent = sys.reason || '';

  const hb = data.scheduler_heartbeat || {};
  document.getElementById('heroLastTick').textContent = hb.last_attempted_tick || 'never';
  document.getElementById('heroNextTick').textContent = hb.next_run_at_self_reported || '—';
  const lastAction = (data.activity_log || []).find(e => e.action !== 'error');
  document.getElementById('heroLastAction').textContent = lastAction
    ? `${lastAction.actor}/${lastAction.action} (${lastAction.ts})` : 'none yet';
  const chk = data.next_checkpoint || {};
  document.getElementById('heroNextCheckpoint').textContent = chk.label
    ? `${chk.label} — ${chk.eta || chk.at || ''}` : 'none scheduled';
  document.getElementById('heroSchedulerHealth').textContent = !hb.ever_run ? 'never run yet'
    : (hb.consecutive_failures > 0 ? `${hb.consecutive_failures} consecutive failure(s)` : 'healthy');

  // ---- 10. Founder action center ----
  function renderQueue(elId, items, emptyText) {
    const el = document.getElementById(elId);
    el.innerHTML = '';
    if (!items.length) {
      el.innerHTML = `<p class="muted">${emptyText}</p>`;
      return;
    }
    items.forEach(a => {
      const d = document.createElement('div');
      d.className = 'finding';
      const est = a.estimated_minutes ? `<p class="muted">Estimated time: ~${a.estimated_minutes} min</p>` : '';
      d.innerHTML = `<strong>[${a.boundary}] ${a.what}</strong><p>${a.why}</p>` +
        `<p><em>${a.founder_action}</em></p>` + est +
        `<p class="muted">Everything else continues autonomously while this is unresolved.</p>` +
        (a.resolved_at ? `<p class="muted">resolved ${a.resolved_at}</p>` : '');
      el.appendChild(d);
    });
  }
  // 2026-09-16: pending_approvals is the FULL queue (may hold more than one
  // genuinely independent approval at once, e.g. a stuck Instagram upload
  // alongside a separate TikTok consent request) -- shown here as its own
  // list via the same renderQueue helper used for queued_actions, mapped
  // onto the same {boundary, what, why, founder_action, estimated_minutes}
  // shape (pending_approvals uses "reason" where queued_actions uses
  // "boundary"). data.pending_approval (singular, the single card above)
  // is unchanged and still drives the primary Approve/Done button flow.
  const pendingApprovals = data.pending_approvals || [];
  renderQueue('pendingApprovalsList', pendingApprovals.map(a => ({
    boundary: a.reason, what: a.what, why: a.why,
    founder_action: a.founder_action, estimated_minutes: a.estimated_minutes,
  })), 'None right now.');

  const allQueued = v.queued_actions || [];
  const statusOf = a => a.status !== 'WAITING_FOR_FOUNDER_CONSENT' ? 'HISTORICAL_RESOLVED'
    : (a.urgency === 'non_urgent' ? 'NON_URGENT' : 'BLOCKING_NOW');
  const blocking = allQueued.filter(a => statusOf(a) === 'BLOCKING_NOW');
  const nonUrgent = allQueued.filter(a => statusOf(a) === 'NON_URGENT');
  renderQueue('queuedBlocking', blocking, 'None right now.');
  renderQueue('queuedNonUrgent', nonUrgent, 'None.');
  renderQueue('queuedHistorical', allQueued.filter(a => statusOf(a) === 'HISTORICAL_RESOLVED'), 'None yet.');
  document.getElementById('noFounderAction').hidden =
    (pendingApprovals.length + blocking.length + nonUrgent.length) > 0;

  // ---- 2. Live autonomous activity feed ----
  const feedEl = document.getElementById('activityFeed');
  feedEl.innerHTML = '';
  (data.activity_feed || []).forEach(e => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="feed-time">${e.ts}</span> ` +
      `<span class="feed-tag feed-${e.category.replace(/\s+/g, '-').toLowerCase()}">${e.category}</span> ` +
      `<span>${e.detail}</span>`;
    feedEl.appendChild(li);
  });

  // ---- 11. Autonomy queue ----
  const naEl = document.getElementById('nextActionsQueue');
  naEl.innerHTML = '';
  (data.next_actions_queue || []).forEach(s => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="status-tag status-${s.status.toLowerCase()}">${s.status}</span> ${s.step}`;
    naEl.appendChild(li);
  });

  // ---- 12. Scheduler verification ----
  document.getElementById('schedRegistered').textContent = hb.ever_run ? 'yes' : 'unknown (never reported)';
  document.getElementById('schedEnabled').textContent = hb.scheduler_enabled_self_reported === null
    || hb.scheduler_enabled_self_reported === undefined ? '—' : (hb.scheduler_enabled_self_reported ? 'yes' : 'no');
  document.getElementById('schedLastAttempt').textContent = hb.last_attempted_tick || 'never';
  document.getElementById('schedLastSuccess').textContent = hb.last_successful_tick || 'never';
  document.getElementById('schedNext').textContent = hb.next_run_at_self_reported || '—';
  document.getElementById('schedDuration').textContent = hb.last_tick_duration_s !== undefined && hb.last_tick_duration_s !== null
    ? `${hb.last_tick_duration_s}s` : '—';
  document.getElementById('schedOutcome').textContent = hb.last_tick_outcome || '—';
  document.getElementById('schedFailures').textContent = hb.consecutive_failures !== undefined ? hb.consecutive_failures : '—';

  const sth = data.scheduler_trigger_health || {};
  const sthEl = document.getElementById('schedTriggerBadge');
  sthEl.textContent = sth.status || '—';
  sthEl.className = 'verdict-badge verdict-sth-' + (sth.status || '').toLowerCase();
  document.getElementById('schedTriggerDetail').textContent = sth.detail || '';

  const tph = data.tool_permission_health || {};
  const tphEl = document.getElementById('toolPermissionBadge');
  tphEl.textContent = tph.status || '—';
  tphEl.className = 'verdict-badge verdict-tph-' + (tph.status || '').toLowerCase();
  document.getElementById('toolPermissionDetail').textContent = tph.detail || '';

  const cih = data.fanvue_chat_ingestion_health || {};
  const cihEl = document.getElementById('chatIngestionBadge');
  cihEl.textContent = cih.status || '—';
  cihEl.className = 'verdict-badge verdict-cih-' + (cih.status || '').toLowerCase();
  document.getElementById('chatIngestionDetail').textContent = cih.detail || '';

  const srl = data.sales_response_loop_health || {};
  const srlEl = document.getElementById('salesLoopBadge');
  srlEl.textContent = srl.status || '—';
  srlEl.className = 'verdict-badge verdict-srl-' + (srl.status || '').toLowerCase();
  document.getElementById('salesLoopDetail').textContent = srl.detail || '';

  const sm = data.fanvue_sales_metrics || {};
  const fmt = (x, suffix) => (x === 'NOT_AVAILABLE' || x === undefined) ? 'NOT_AVAILABLE'
    : (suffix === '%' ? `${Math.round(x * 100)}%` : `${x}${suffix || ''}`);
  document.getElementById('salesGenuineThreads').textContent = fmt(sm.genuine_inbound_dm_threads);
  document.getElementById('salesSpamThreads').textContent = fmt(sm.spam_scam_dm_threads);
  document.getElementById('salesSpamRate').textContent = fmt(sm.spam_scam_rate, '%');
  document.getElementById('salesResponseRate').textContent = fmt(sm.response_rate, '%');
  document.getElementById('salesLatency').textContent = fmt(sm.median_response_latency_minutes, ' min');
  document.getElementById('salesOfferRate').textContent = fmt(sm.offer_ppv_rate, '%');
  document.getElementById('salesConversion').textContent = fmt(sm.conversion_to_paid, '%');
  document.getElementById('salesRepeat').textContent = fmt(sm.repeat_purchase_rate, '%');
  document.getElementById('salesMetricsNote').textContent = sm.note || '';

  // ---- CEO bottleneck ----
  const acc = data.manager_accountability || {};
  document.getElementById('bottleneckText').textContent = fmtVal(acc.current_bottleneck) || '—';
  document.getElementById('bottleneckAt').textContent = acc.bottleneck_identified_at && acc.bottleneck_identified_at !== 'NOT_AVAILABLE'
    ? `identified ${acc.bottleneck_identified_at}` : '';

  // ---- Business scoreboard ----
  const sb = data.business_scoreboard || {};
  document.getElementById('sbRevenue').textContent = fmtUsd(sb.gross_revenue_usd);
  document.getElementById('sbSubs').textContent = fmtVal(sb.paid_subscribers);
  document.getElementById('sbRevPerPayer').textContent = sb.revenue_per_payer === 'NOT_AVAILABLE' ? 'NOT AVAILABLE' : fmtUsd(sb.revenue_per_payer);
  document.getElementById('sbConversion').textContent = fmt(sb.conversion_to_paid, '%');
  document.getElementById('sbRepeat').textContent = fmt(sb.repeat_purchase_rate, '%');
  document.getElementById('sbFounderMin').textContent = fmtVal(sb.founder_minutes_this_week) + ` (target ${sb.founder_target_minutes_per_week || '30-60'})`;
  document.getElementById('sbExpRunning').textContent = fmtVal(sb.experiments_running);
  document.getElementById('sbExpConcluded').textContent = fmtVal(sb.experiments_concluded);

  // ---- Stall detection ----
  const stall = data.stall_detection || {};
  const stallBanner = document.getElementById('stallBanner');
  const stallFlagsEl = document.getElementById('stallFlags');
  stallFlagsEl.innerHTML = '';
  if (!stall.stalled) {
    stallBanner.hidden = false;
  } else {
    stallBanner.hidden = true;
    (stall.flags || []).forEach(f => {
      const li = document.createElement('li');
      li.textContent = f;
      stallFlagsEl.appendChild(li);
    });
  }

  // ---- Manager accountability grid ----
  const maGrid = document.getElementById('managerAccountabilityGrid');
  maGrid.innerHTML = '';
  const managers = acc.managers || {};
  [['venture', 'Venture (CEO)'], ['growth', 'Growth'], ['content', 'Content'], ['sales', 'Sales']].forEach(([key, label]) => {
    const m = managers[key] || {};
    const col = document.createElement('div');
    col.className = 'specialist-col';
    col.innerHTML = `<h3>${label}</h3>` +
      `<p class="muted">Objective: ${fmtVal(m.current_objective)}</p>` +
      `<p class="muted">Blocker: ${fmtVal(m.current_blocker)}</p>` +
      `<p class="muted">Next: ${fmtVal(m.next_action)} ${m.next_action_at && m.next_action_at !== 'NOT_AVAILABLE' ? '@ ' + m.next_action_at : ''}</p>` +
      `<p class="muted">Last result: ${fmtVal(m.last_action_result)}</p>`;
    maGrid.appendChild(col);
  });

  // ---- Content pipeline health ----
  const cph = data.content_pipeline_health || {};
  const cphGrid = document.getElementById('contentPipelineGrid');
  cphGrid.innerHTML = '';
  const LIFECYCLE_ORDER = ['BRIEF_READY', 'ASSET_GENERATED', 'QA_PASSED', 'PUBLISH_READY', 'SCHEDULED', 'PUBLISHED', 'DEFERRED'];
  Object.entries(cph.by_platform || {}).forEach(([platform, counts]) => {
    const stat = document.createElement('div');
    stat.className = 'stat';
    const parts = LIFECYCLE_ORDER.filter(s => counts[s]).map(s => `${counts[s]} ${s}`);
    stat.innerHTML = `<span class="stat-label">${platform}</span>` +
      `<span class="stat-value">${parts.join(' · ') || 'none'}</span>`;
    cphGrid.appendChild(stat);
  });
  document.getElementById('contentPipelineNote').textContent = cph.note || '';

  const indepEl = document.getElementById('schedIndependenceNote');
  if (!hb.ever_run) {
    indepEl.textContent = 'Independent execution has NOT yet been observed -- no scheduled tick has reported in since this heartbeat mechanism was added. The scheduled task exists, but that alone does not prove it runs unattended.';
  } else {
    indepEl.textContent = `Independent execution confirmed: a scheduled tick self-reported at ${hb.last_attempted_tick} with no conversation open.`;
  }

  document.getElementById('hypothesisConcept').textContent = v.hypothesis.concept || '';
  document.getElementById('hypothesisConstraint').textContent = 'Primary constraint: ' + (v.hypothesis.primary_constraint || '');

  // ---- 3. Experiments ----
  const expListEl = document.getElementById('experimentsList');
  const experiments = v.experiments || [];
  expListEl.innerHTML = '';
  if (!experiments.length) {
    expListEl.innerHTML = '<p class="muted">None recorded.</p>';
  }
  experiments.slice().reverse().forEach(e => {
    const d = document.createElement('div');
    d.className = 'finding';
    const statusBadge = `<span class="pill pill-${e.status}">${e.status.toUpperCase()}</span>`;
    let elapsed = '';
    if (e.published_at) {
      const start = new Date(e.published_at.replace(' ', 'T'));
      if (!isNaN(start)) {
        const hrs = Math.round((Date.now() - start.getTime()) / 3600000 * 10) / 10;
        elapsed = `<p class="muted">Elapsed: ~${hrs}h</p>`;
      }
    }
    d.innerHTML = `<strong>${e.id}</strong> ${statusBadge}` +
      `<p>${e.hypothesis}</p>` +
      `<p class="muted">Variable: ${e.variable} · Control: ${e.control}</p>` +
      `<p class="muted">Channels: ${(e.channels || []).join(', ')} · Published: ${e.published_at || '—'} · Window ends: ${e.observation_window_end || '—'}</p>` +
      elapsed +
      `<p class="muted">Success metric: ${e.success_metric}</p>` +
      (e.guardrails ? `<p class="muted">Guardrails: ${e.guardrails}</p>` : '') +
      (e.result ? `<p><strong>Result:</strong> ${e.result} (confidence: ${e.confidence}) — next: ${e.next_decision}</p>` : '') +
      ((e.measurements || []).length
        ? `<p class="muted">Current evidence: ${JSON.stringify(e.measurements[e.measurements.length - 1])}</p>`
        : '<p class="muted">Current evidence: none recorded yet.</p>');
    expListEl.appendChild(d);
  });

  // ---- 4. Funnel ----
  const funnelEl = document.getElementById('funnelView');
  funnelEl.innerHTML = '';
  (data.funnel || []).forEach((f, i) => {
    const row = document.createElement('div');
    row.className = 'funnel-row';
    row.innerHTML = `<span class="funnel-stage">${f.stage}</span><span class="funnel-value">${fmtVal(f.value)}</span>`;
    funnelEl.appendChild(row);
    if (i < data.funnel.length - 1) {
      const arrow = document.createElement('div');
      arrow.className = 'funnel-arrow';
      arrow.textContent = '↓';
      funnelEl.appendChild(arrow);
    }
  });

  // ---- Recent commercial events ----
  const ceEl = document.getElementById('commercialEvents');
  ceEl.innerHTML = '';
  const commercialEvents = data.commercial_events || [];
  if (!commercialEvents.length) {
    ceEl.innerHTML = '<p class="muted">No commercial events discovered yet.</p>';
  }
  commercialEvents.forEach(e => {
    const d = document.createElement('div');
    d.className = 'finding';
    d.innerHTML = `<strong>${e.type}</strong> <span class="status-tag status-${e.lifecycle_status.toLowerCase()}">${e.lifecycle_status.replace('_', ' ')}</span>` +
      `<p class="muted">${e.ts} · ${e.handle || 'unknown fan'} · acquisition: ${e.acquisition_source}</p>` +
      (e.sales_manager_note ? `<p>${e.sales_manager_note}</p>` : '');
    ceEl.appendChild(d);
  });

  // ---- 5. Platform health ----
  const ph = data.platform_health || {};
  const phEl = document.getElementById('platformHealth');
  phEl.innerHTML = '';
  [['instagram', 'Instagram'], ['tiktok', 'TikTok'], ['fanvue', 'Fanvue']].forEach(([key, label]) => {
    const p = ph[key] || {};
    const col = document.createElement('div');
    col.className = 'specialist-col';
    let extra = '';
    if (key === 'fanvue') {
      extra = `<p class="muted">Discoverable: ${p.is_discoverable ? 'yes' : 'no'} · Price: ${p.subscription_price_usd ? '$' + p.subscription_price_usd : '—'}</p>` +
        `<p class="muted">Promotion: ${p.promotion ? p.promotion.type : 'none'} · Vault posts: ${p.vault_posts}</p>`;
    }
    col.innerHTML = `<h3>${label}</h3>` +
      `<p class="muted">Connected: ${p.connected ? 'yes' : 'no'}</p>` + extra +
      `<p class="muted">In experiments: ${(p.in_experiments || []).join(', ') || 'none'}</p>` +
      (p.known_limitations && p.known_limitations.length
        ? `<p class="muted">Known limitation: ${p.known_limitations[p.known_limitations.length - 1]}</p>` : '') +
      (p.pending_actions && p.pending_actions.length
        ? `<p><em>Pending: ${p.pending_actions.join('; ')}</em></p>` : '<p class="muted">No action pending.</p>');
    phEl.appendChild(col);
  });

  // ---- 6. Specialists + review badges ----
  const specialists = v.specialists || {};
  [['growth', 'Growth'], ['content', 'Content'], ['sales', 'Sales']].forEach(([key, label]) => {
    const s = specialists[key] || {};
    document.getElementById(key + 'LastRun').textContent =
      s.last_run_at ? `last run: ${s.last_run_at}` : 'not yet run';
    document.getElementById(key + 'Summary').textContent = s.summary || '';
    const recsEl = document.getElementById(key + 'Recs');
    recsEl.innerHTML = '';
    (s.priority_order || []).slice(0, 3).forEach((item, i) => {
      const p = document.createElement('p');
      p.className = 'muted';
      p.textContent = `${i + 1}. ${item}`;
      recsEl.appendChild(p);
    });
    const reviewEl = document.getElementById(key + 'Review');
    const review = s.venture_manager_review;
    reviewEl.innerHTML = review
      ? `<span class="pill pill-review-${review.disposition}">${review.disposition.toUpperCase()}</span> <span class="muted">${review.reason}</span>`
      : '<span class="muted">Venture Manager has not reviewed this audit yet.</span>';
  });

  // ---- 7. Venture Manager decision log ----
  const decisionEl = document.getElementById('decisionLog');
  const decisions = (data.activity_log || []).filter(e =>
    e.actor === 'venture_manager' || e.action.includes('arbitration') || e.action.includes('decision'));
  decisionEl.innerHTML = '';
  if (!decisions.length) {
    decisionEl.innerHTML = '<p class="muted">No arbitration decisions recorded yet.</p>';
  }
  decisions.slice(0, 10).forEach(d => {
    const el = document.createElement('div');
    el.className = 'finding';
    el.innerHTML = `<span class="muted">${d.ts}</span><p>${d.detail}</p>`;
    decisionEl.appendChild(el);
  });

  // ---- 8. H1/H2 scorecard ----
  const h1 = data.h1_status || {};
  document.getElementById('h1Badge').textContent = h1.status || '—';
  document.getElementById('h1Badge').className = 'verdict-badge verdict-h1-' + (h1.status || '').toLowerCase();
  document.getElementById('h1Evidence').textContent = h1.evidence || '';

  const h2 = data.h2_status || {};
  document.getElementById('h2Badge').textContent = h2.status || '—';
  document.getElementById('h2Badge').className = 'verdict-badge verdict-h2-' + (h2.status || '').toLowerCase();
  document.getElementById('opSetup').textContent = h2.setup_overhead_minutes_cumulative + ' min';
  document.getElementById('opWeek').textContent = h2.operational_minutes_this_week + ' min';
  document.getElementById('opCumulative').textContent = h2.operational_minutes_cumulative + ' min';
  document.getElementById('opNote').textContent = (v.thresholds.operability && v.thresholds.operability.note) || '';
  document.getElementById('cndCount').textContent = `${h2.could_not_delegate_count || 0} automation-limitation entr${(h2.could_not_delegate_count === 1) ? 'y' : 'ies'} logged.`;

  const cndEl = document.getElementById('couldNotDelegate');
  cndEl.innerHTML = '';
  (v.metrics.h2_operability.could_not_delegate_log || []).slice(-5).reverse().forEach(e => {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = `${e.task}: ${e.why_not_delegated} (tried: ${e.tier_attempted})`;
    cndEl.appendChild(p);
  });

  // ---- 9. Economics ----
  const h1m = v.metrics.h1_market;
  document.getElementById('mRevenue').textContent = fmtUsd(h1m.gross_revenue_usd);
  document.getElementById('mSpend').textContent = fmtUsd(h1m.spend_usd);
  const econ = data.unit_economics || {};
  document.getElementById('econRevPer1k').textContent = fmtEcon(econ.revenue_per_1000_impressions);
  document.getElementById('econCac').textContent = fmtEcon(econ.cac_usd);
  document.getElementById('econArppu').textContent = fmtEcon(econ.arppu_usd);
  document.getElementById('econLtv').textContent = fmtEcon(econ.approximate_ltv_usd);
  document.getElementById('econNote').textContent = econ._note || '';

  const cb = v.credit_budget || {session_limit: 0, spent_this_session: 0};
  document.getElementById('creditsSpent').textContent = cb.spent_this_session;
  document.getElementById('creditsLimit').textContent = cb.session_limit;
  document.getElementById('creditsRemaining').textContent =
    (cb.session_limit - cb.spent_this_session).toFixed(2);

  const findingsEl = document.getElementById('latestFindings');
  findingsEl.innerHTML = '';
  const research = v.research_log || [];
  const compliance = v.compliance_log || [];
  if (!research.length && !compliance.length) {
    findingsEl.innerHTML = '<p class="muted">No research/compliance findings yet.</p>';
  }
  research.slice(-3).reverse().forEach(r => {
    const d = document.createElement('div');
    d.className = 'finding';
    d.innerHTML = `<strong>${r.question}</strong><p>${r.findings}</p><p class="muted">confidence: ${r.confidence}, QA: ${r.qa_verdict}</p>`;
    findingsEl.appendChild(d);
  });
  compliance.slice(-3).reverse().forEach(c => {
    const d = document.createElement('div');
    d.className = 'finding';
    d.innerHTML = `<strong>Compliance — ${c.platform}: ${c.action_checked}</strong><p>Verdict: ${c.verdict}</p>`;
    findingsEl.appendChild(d);
  });

  const approvalCard = document.getElementById('approvalCard');
  if (data.pending_approval) {
    approvalCard.hidden = false;
    document.getElementById('approvalWhat').textContent = data.pending_approval.what;
    document.getElementById('approvalWhy').textContent = data.pending_approval.why;
    document.getElementById('approvalAction').textContent = data.pending_approval.founder_action;
    document.getElementById('approvalMinutes').textContent = data.pending_approval.estimated_minutes;
    const isGoKill = data.pending_approval.reason === 'MAJOR_GO_KILL_DECISION';
    document.getElementById('goKillButtons').hidden = !isGoKill;
    document.getElementById('plainApproveButtons').hidden = isGoKill;
  } else {
    approvalCard.hidden = true;
  }

  const logEl = document.getElementById('activityLog');
  logEl.innerHTML = '';
  data.activity_log.forEach(e => {
    const li = document.createElement('li');
    li.textContent = `[${e.ts}] ${e.actor}/${e.action}: ${e.detail}`;
    logEl.appendChild(li);
  });
}

async function approve(answer) {
  await fetch('/api/approve', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({answer}),
  });
  render(await fetchState());
}

document.getElementById('approveBtn').addEventListener('click', () => approve('approve'));
document.querySelectorAll('#goKillButtons button').forEach(btn => {
  btn.addEventListener('click', () => approve(btn.dataset.answer));
});

fetchState().then(render);
setInterval(() => fetchState().then(render), 15000);
