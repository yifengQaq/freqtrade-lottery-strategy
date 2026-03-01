/**
 * V2ex-Agent Dashboard — Frontend Logic
 *
 * Auto-refreshes every 5s, renders charts with Chart.js,
 * and provides real-time monitoring of the LLM backtest agent.
 */

// ===== State =====
let scoreChart = null;
let windowBarChart = null;
let windowRadarChart = null;
let autoRefreshTimer = null;
let currentTab = 'dashboard';
const REFRESH_INTERVAL = 5000;

// ===== Initialization =====
document.addEventListener('DOMContentLoaded', () => {
  refreshAll();
  startAutoRefresh();
});

// ===== Tab Switching =====
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
  document.getElementById(`tab-${tab}`).classList.add('active');

  // Load tab-specific data
  if (tab === 'log') loadLog();
  if (tab === 'strategy') loadVersions();
  if (tab === 'windows') loadMetrics();
}

// ===== Auto Refresh =====
function startAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  let countdown = REFRESH_INTERVAL / 1000;

  const countdownEl = document.getElementById('refresh-countdown');
  autoRefreshTimer = setInterval(() => {
    countdown--;
    if (countdownEl) countdownEl.textContent = countdown;
    if (countdown <= 0) {
      countdown = REFRESH_INTERVAL / 1000;
      refreshAll();
    }
  }, 1000);
}

function toggleAutoRefresh() {
  const checked = document.getElementById('auto-refresh-toggle').checked;
  if (checked) {
    startAutoRefresh();
  } else {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  }
}

// ===== Refresh All =====
async function refreshAll() {
  try {
    await Promise.all([
      loadStatus(),
      loadHistory(),
      loadRounds(),
    ]);

    // Load tab-specific data if visible
    if (currentTab === 'windows') loadMetrics();
    if (currentTab === 'log') loadLog();
  } catch (e) {
    console.error('Refresh failed:', e);
  }
}

// ===== API: Status =====
async function loadStatus() {
  const res = await fetch('/api/status');
  const data = await res.json();

  // Status badge
  const badge = document.getElementById('status-badge');
  badge.textContent = data.running ? 'RUNNING' : 'STOPPED';
  badge.className = `badge ${data.running ? 'running' : 'stopped'}`;

  // Control buttons
  document.getElementById('btn-start').disabled = data.running;
  document.getElementById('btn-stop').disabled = !data.running;

  // Stats
  document.getElementById('stat-epoch').textContent = data.current_epoch || '-';
  document.getElementById('stat-round').textContent = data.current_round || '-';
  document.getElementById('stat-success').textContent = data.success_rate || '-';
  document.getElementById('stat-fixes').textContent = data.auto_fixes || '0';
  document.getElementById('stat-best').textContent =
    data.best_score ? `R${data.best_round} (${data.best_score.toFixed(2)})` : '-';
  document.getElementById('stat-uptime').textContent = data.uptime || '-';
}

// ===== API: History (Score Chart) =====
async function loadHistory() {
  const res = await fetch('/api/history');
  const data = await res.json();
  const records = data.records || [];

  renderScoreChart(records);
}

function renderScoreChart(records) {
  const ctx = document.getElementById('score-chart');
  if (!ctx) return;

  const labels = records.map(r => `R${r.round}`);
  const evalScores = records.map(r => r.eval_score > 0 ? r.eval_score : null);  // null = skip point
  const gapScores = records.map(r => r.weighted_norm || 0);

  // Find epoch boundaries (where round resets)
  const epochAnnotations = [];
  for (let i = 1; i < records.length; i++) {
    if (records[i].round <= records[i-1].round) {
      epochAnnotations.push(i);
    }
  }

  const maxEval = Math.max(...evalScores.filter(s => s !== null && s > 0));

  if (scoreChart) scoreChart.destroy();

  scoreChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: '评估分 (越高越好)',
          data: evalScores,
          borderColor: '#3fb950',
          backgroundColor: 'rgba(63,185,80,0.1)',
          borderWidth: 2,
          pointRadius: evalScores.map((s) => s === maxEval ? 6 : s ? 3 : 0),
          pointBackgroundColor: evalScores.map((s) =>
            s === maxEval ? '#f0883e' : '#3fb950'
          ),
          fill: true,
          tension: 0.3,
          spanGaps: true,
          yAxisID: 'y',
        },
        {
          label: '目标距离 (越低越好)',
          data: gapScores,
          borderColor: '#58a6ff',
          backgroundColor: 'rgba(88,166,255,0.05)',
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 2,
          fill: false,
          tension: 0.3,
          yAxisID: 'y1',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          labels: { color: '#8b949e' },
          position: 'top',
        },
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              if (ctx.datasetIndex !== 0) return '';
              const r = records[ctx.dataIndex];
              const d = r.deltas || {};
              return [
                `月均利润差: ${(d.monthly_net_profit_avg || 0).toFixed(1)}`,
                `最大月亏差: ${(d.max_monthly_loss || 0).toFixed(1)}`,
                `最大回撤差: ${(d.max_drawdown_pct || 0).toFixed(1)}%`,
              ].join('\n');
            }
          }
        }
      },
      scales: {
        y: {
          type: 'linear',
          position: 'left',
          grid: { color: 'rgba(48,54,61,0.5)' },
          ticks: { color: '#3fb950' },
          title: { display: true, text: '评估分', color: '#3fb950' },
        },
        y1: {
          type: 'linear',
          position: 'right',
          grid: { drawOnChartArea: false },
          ticks: { color: '#58a6ff' },
          title: { display: true, text: '目标距离', color: '#58a6ff' },
        },
        x: {
          grid: { color: 'rgba(48,54,61,0.3)' },
          ticks: { color: '#8b949e', maxTicksLimit: 30 },
        }
      },
    },
  });

  // Set canvas height
  ctx.parentElement.style.height = '300px';
}

// ===== API: Rounds Table =====
async function loadRounds() {
  const res = await fetch('/api/rounds');
  const data = await res.json();
  const rounds = (data.rounds || []).reverse(); // newest first
  const epochResets = data.epoch_resets || [];

  renderRoundsTable(rounds);
  renderEpochTimeline(epochResets);
}

function renderRoundsTable(rounds) {
  const tbody = document.getElementById('rounds-body');
  if (!tbody) return;

  tbody.innerHTML = rounds.map(r => {
    const statusClass = r.status === 'success' ? 'status-success' :
                        r.status === 'failed' ? 'status-failed' : 'status-running';
    const statusIcon = r.status === 'success' ? '✅' :
                       r.status === 'failed' ? '❌' : '⏳';
    const fixIcon = r.auto_fix ? '🔧' : '';
    const evalScore = r.eval_score ? r.eval_score.toFixed(2) : '-';

    // Profit metrics
    const profit = fmtNum(r.profit_pct, 2, true);
    const maxDD = fmtNum(r.max_dd_pct, 1, false, true);
    const trades = r.total_trades != null ? r.total_trades : '-';
    const winRate = r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '-';
    const sharpe = fmtNum(r.sharpe, 2, true);
    const pf = fmtNum(r.profit_factor, 2);

    return `<tr>
      <td>${r.epoch || '-'}</td>
      <td><strong>R${r.round}</strong></td>
      <td>${evalScore}</td>
      <td>${profit}</td>
      <td>${maxDD}</td>
      <td>${trades}</td>
      <td>${winRate}</td>
      <td>${sharpe}</td>
      <td>${pf}</td>
      <td class="${statusClass}">${statusIcon}</td>
      <td>${fixIcon}</td>
      <td title="${r.description || ''}">${truncate(r.description || '-', 50)}</td>
    </tr>`;
  }).join('');
}

function renderEpochTimeline(epochs) {
  const container = document.getElementById('epoch-timeline');
  if (!container) return;

  if (epochs.length === 0) {
    container.innerHTML = '<p style="color:var(--text-secondary)">暂无 Epoch 重置记录</p>';
    return;
  }

  container.innerHTML = epochs.map(e => `
    <div class="epoch-card">
      <div class="epoch-title">Epoch ${e.epoch} 重置</div>
      <div class="epoch-detail">
        最佳轮: R${e.best_round}<br>
        最佳分: ${e.best_score.toFixed(2)}<br>
        末轮分: ${e.scores}<br>
        时间: ${e.timestamp}
      </div>
    </div>
  `).join('');
}

// ===== API: Metrics (Multi-Window) =====
async function loadMetrics() {
  const res = await fetch('/api/metrics');
  const data = await res.json();

  if (!data.matrix) {
    document.getElementById('window-metrics-table').innerHTML =
      '<p style="color:var(--text-secondary)">暂无多窗口回测数据</p>';
    return;
  }

  const matrix = data.matrix;
  const windows = matrix.windows || [];
  const metricsByWindow = matrix.metrics_by_window || {};

  renderWindowBarChart(windows, metricsByWindow);
  renderWindowRadarChart(windows, metricsByWindow);
  renderWindowMetricsTable(windows, metricsByWindow);
}

function renderWindowBarChart(windows, metricsByWindow) {
  const ctx = document.getElementById('window-bar-chart');
  if (!ctx) return;

  const profits = windows.map(w => {
    const m = metricsByWindow[w] || {};
    return m.total_profit_pct || 0;
  });

  const colors = profits.map(p => p >= 0 ? 'rgba(63,185,80,0.7)' : 'rgba(248,81,73,0.7)');

  if (windowBarChart) windowBarChart.destroy();

  windowBarChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: windows.map(formatWindowName),
      datasets: [{
        label: '总收益 %',
        data: profits,
        backgroundColor: colors,
        borderColor: colors.map(c => c.replace('0.7', '1')),
        borderWidth: 1,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
      },
      scales: {
        y: {
          grid: { color: 'rgba(48,54,61,0.5)' },
          ticks: { color: '#8b949e' },
          title: { display: true, text: '收益 %', color: '#8b949e' },
        },
        x: {
          grid: { display: false },
          ticks: { color: '#8b949e' },
        }
      }
    }
  });
}

function renderWindowRadarChart(windows, metricsByWindow) {
  const ctx = document.getElementById('window-radar-chart');
  if (!ctx) return;

  // Normalize metrics for radar: sharpe, sortino, win_rate, profit_factor, calmar
  const radarMetrics = ['sharpe_ratio', 'sortino_ratio', 'win_rate', 'profit_factor'];
  const labels = ['Sharpe', 'Sortino', 'Win Rate', 'Profit Factor'];

  const datasets = windows.map((w, i) => {
    const m = metricsByWindow[w] || {};
    const hue = (i * 360 / windows.length) % 360;
    return {
      label: formatWindowName(w),
      data: radarMetrics.map(key => {
        let val = m[key] || 0;
        // Normalize to 0-10 scale roughly
        if (key === 'win_rate') val *= 10;
        if (key === 'sharpe_ratio') val = Math.max(0, val * 3);
        if (key === 'sortino_ratio') val = Math.max(0, val);
        if (key === 'profit_factor') val = Math.max(0, val * 2);
        return Math.min(10, val);
      }),
      borderColor: `hsl(${hue}, 70%, 60%)`,
      backgroundColor: `hsla(${hue}, 70%, 60%, 0.1)`,
      pointBackgroundColor: `hsl(${hue}, 70%, 60%)`,
      borderWidth: 2,
    };
  });

  if (windowRadarChart) windowRadarChart.destroy();

  windowRadarChart = new Chart(ctx, {
    type: 'radar',
    data: { labels, datasets },
    options: {
      responsive: true,
      scales: {
        r: {
          grid: { color: 'rgba(48,54,61,0.5)' },
          angleLines: { color: 'rgba(48,54,61,0.5)' },
          ticks: { color: '#8b949e', backdropColor: 'transparent' },
          pointLabels: { color: '#8b949e' },
        }
      },
      plugins: {
        legend: {
          labels: { color: '#8b949e' },
          position: 'bottom',
        }
      }
    }
  });
}

function renderWindowMetricsTable(windows, metricsByWindow) {
  const container = document.getElementById('window-metrics-table');
  if (!container) return;

  const keys = [
    'total_profit_pct', 'max_drawdown_pct', 'sharpe_ratio',
    'sortino_ratio', 'profit_factor', 'win_rate', 'total_trades',
    'avg_profit_per_trade_pct', 'backtest_days',
  ];

  const keyLabels = {
    total_profit_pct: '总收益 %',
    max_drawdown_pct: '最大回撤 %',
    sharpe_ratio: 'Sharpe',
    sortino_ratio: 'Sortino',
    profit_factor: '盈亏比',
    win_rate: '胜率',
    total_trades: '交易次数',
    avg_profit_per_trade_pct: '均笔收益 %',
    backtest_days: '回测天数',
  };

  let html = '<table class="metrics-table"><thead><tr><th>指标</th>';
  windows.forEach(w => { html += `<th>${formatWindowName(w)}</th>`; });
  html += '</tr></thead><tbody>';

  keys.forEach(key => {
    html += `<tr><td>${keyLabels[key] || key}</td>`;
    windows.forEach(w => {
      const m = metricsByWindow[w] || {};
      let val = m[key];
      if (val === undefined) val = '-';
      else if (typeof val === 'number') {
        const cls = key.includes('drawdown') ? (val > 30 ? 'negative' : '') :
                    (val >= 0 ? 'positive' : 'negative');
        val = `<span class="${cls}">${val.toFixed(2)}</span>`;
      }
      html += `<td>${val}</td>`;
    });
    html += '</tr>';
  });

  html += '</tbody></table>';
  container.innerHTML = html;
}

// ===== API: Strategy Versions & Diff =====
async function loadVersions() {
  const res = await fetch('/api/versions');
  const data = await res.json();
  const versions = data.versions || [];

  const selectA = document.getElementById('diff-a');
  const selectB = document.getElementById('diff-b');

  // Preserve current selections
  const prevA = selectA.value;
  const prevB = selectB.value;

  const options = versions.map(v =>
    `<option value="${v.filename}">R${v.round} — ${truncate(v.description, 50)}</option>`
  ).join('');

  selectA.innerHTML = options;
  selectB.innerHTML = options;

  // Restore or set defaults
  if (prevA && versions.find(v => v.filename === prevA)) {
    selectA.value = prevA;
  } else if (versions.length >= 2) {
    selectA.value = versions[versions.length - 2].filename;
  }

  if (prevB && versions.find(v => v.filename === prevB)) {
    selectB.value = prevB;
  } else if (versions.length >= 1) {
    selectB.value = versions[versions.length - 1].filename;
  }
}

async function loadDiff() {
  const a = document.getElementById('diff-a').value;
  const b = document.getElementById('diff-b').value;

  if (!a || !b) {
    document.getElementById('diff-output').innerHTML =
      '<p style="color:var(--text-secondary)">请选择两个版本进行对比</p>';
    return;
  }

  try {
    const res = await fetch(`/api/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    const data = await res.json();

    if (data.diff_html) {
      document.getElementById('diff-output').innerHTML = data.diff_html;
    } else {
      document.getElementById('diff-output').innerHTML =
        `<pre>${escapeHtml(data.diff_text || '无差异')}</pre>`;
    }
  } catch (e) {
    document.getElementById('diff-output').innerHTML =
      `<p style="color:var(--accent-red)">加载失败: ${e.message}</p>`;
  }
}

// ===== API: Log =====
async function loadLog() {
  const tail = document.getElementById('log-tail')?.value || 200;
  const res = await fetch(`/api/log?tail=${tail}`);
  const data = await res.json();

  const container = document.getElementById('log-output');
  if (!container) return;

  container.innerHTML = (data.lines || []).map(colorLogLine).join('\n');

  if (document.getElementById('log-auto-scroll')?.checked) {
    container.scrollTop = container.scrollHeight;
  }
}

function colorLogLine(line) {
  const escaped = escapeHtml(line);
  if (/Epoch.*Round/.test(line)) return `<span class="log-line-round">${escaped}</span>`;
  if (/Epoch.*reset/.test(line)) return `<span class="log-line-epoch">${escaped}</span>`;
  if (/Auto-fixed/.test(line)) return `<span class="log-line-fix">${escaped}</span>`;
  if (/WARNING/.test(line)) return `<span class="log-line-warning">${escaped}</span>`;
  if (/ERROR|failed|NameError/.test(line)) return `<span class="log-line-error">${escaped}</span>`;
  return `<span class="log-line-info">${escaped}</span>`;
}

// ===== API: Controls =====
async function controlAgent(action) {
  if (action === 'stop' && !confirm('确定要停止 Agent 吗？')) return;

  try {
    const res = await fetch(`/api/control/${action}`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || `Agent ${action} 成功`, 'success');
    } else {
      showToast(data.detail || `操作失败`, 'error');
    }
    // Refresh after a short delay
    setTimeout(refreshAll, 2000);
  } catch (e) {
    showToast(`请求失败: ${e.message}`, 'error');
  }
}

// ===== Helpers =====
function fmtNum(val, decimals, colorSign, invertColor) {
  if (val == null || val === undefined) return '-';
  const n = Number(val);
  if (isNaN(n)) return '-';
  const s = n.toFixed(decimals);
  if (colorSign) {
    const positive = invertColor ? n < 0 : n > 0;
    const cls = positive ? 'status-success' : (n < 0 ? 'status-failed' : '');
    return `<span class="${cls}">${s}</span>`;
  }
  if (invertColor) {
    // For drawdown: red if high
    const cls = n > 50 ? 'status-failed' : n > 30 ? 'status-running' : 'status-success';
    return `<span class="${cls}">${s}</span>`;
  }
  return s;
}

function truncate(str, maxLen) {
  if (!str) return '';
  return str.length > maxLen ? str.substring(0, maxLen) + '…' : str;
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatWindowName(w) {
  const map = {
    'bull_2021': '🟢 牛市 2021',
    'bear_2022': '🔴 熊市 2022',
    'sideways_2023': '🟡 震荡 2023',
    'recovery_2024': '🔵 复苏 2024',
    'recent_2025': '⚪ 近期 2025',
  };
  return map[w] || w;
}

function showToast(msg, type) {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position: fixed; top: 80px; right: 24px; z-index: 1000;
    padding: 12px 24px; border-radius: 8px;
    background: ${type === 'success' ? 'rgba(63,185,80,0.9)' : 'rgba(248,81,73,0.9)'};
    color: #fff; font-size: 0.9em; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: fadeIn 0.3s ease;
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}
