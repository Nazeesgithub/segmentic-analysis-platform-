const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");

let featureMeta = { features: [], defaults: {} };
let dashboardData = null;
let businessDecisionData = null;
let kpiData = null;
let predictorMode = "quick";
const NOVA_CHAT_HISTORY_KEY = "segmentiq_nova_chat_history";
let novaChatHistory = [];
let userPortalState = {
  loggedIn: false,
  features: [],
  defaults: {},
  context: null,
};

const demoFeatureMapping = {
  age: ["TENURE"],
  income: ["CREDIT_LIMIT", "PAYMENTS"],
  spendingScore: ["PURCHASES", "PURCHASES_TRX"],
  transactionFrequency: ["PURCHASES_FREQUENCY", "PURCHASES_TRX"],
};

function pickAvailableFeature(meta, candidates) {
  return candidates.find((name) => meta.features.includes(name)) || null;
}

function renderNovaStatus(status) {
  const statusEl = document.getElementById("novaStatus");
  const textEl = document.getElementById("novaStatusText");

  if (!statusEl || !textEl) return;

  statusEl.classList.remove("active", "fallback");
  statusEl.classList.add(status.active ? "active" : "fallback");
  textEl.textContent = `${status.label}: ${status.description}`;
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    panels.forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    const activePanel = document.getElementById(tab.dataset.tab);
    if (!activePanel) return;
    activePanel.classList.add("active");

    if (
      (tab.dataset.tab === "dashboard" || tab.dataset.tab === "analytics") &&
      dashboardData
    ) {
      setTimeout(() => {
        renderDashboardCharts(dashboardData);
      }, 80);
    }

    if (tab.dataset.tab === "decisions" && businessDecisionData) {
      renderBusinessDecisionDashboard(businessDecisionData);
      setTimeout(() => {
        updateDecisionLab();
      }, 80);
    }

    if (tab.dataset.tab === "user") {
      loadUserPortal();
    }
  });
});

function setUserMessage(message, isError = false) {
  const el = document.getElementById("userPortalMessage");
  if (!el) return;
  if (!message) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `<p style="color:${isError ? "#b91c1c" : "#0f766e"};">${message}</p>`;
}

function renderUserStatus() {
  const statusEl = document.getElementById("userPortalStatus");
  const authArea = document.getElementById("userAuthArea");
  const profileArea = document.getElementById("userProfileArea");
  if (!statusEl || !authArea || !profileArea) return;

  if (!userPortalState.loggedIn) {
    statusEl.innerHTML =
      "<p><strong>Status:</strong> Not logged in. Register or login to manage your profile.</p>";
    authArea.style.display = "grid";
    profileArea.style.display = "none";
    return;
  }

  const user = userPortalState.context?.current_user || {};
  statusEl.innerHTML = `<p><strong>Status:</strong> Logged in as ${user.email || "-"}</p>`;
  authArea.style.display = "none";
  profileArea.style.display = "block";
}

function renderUserDataForm() {
  const form = document.getElementById("userDataForm");
  if (!form) return;

  form.innerHTML = "";
  const features = userPortalState.features || [];
  const defaults = userPortalState.defaults || {};
  const saved = userPortalState.context?.saved_profile?.feature_data || {};

  features.forEach((feature) => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.textContent = feature;

    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.name = feature;
    const rawValue = saved[feature] ?? defaults[feature] ?? 0;
    input.value = Number(rawValue).toFixed(4);

    wrap.appendChild(label);
    wrap.appendChild(input);
    form.appendChild(wrap);
  });
}

async function loadUserPortal() {
  try {
    const status = await getJson("/api/user/status");
    userPortalState.loggedIn = !!status.loggedIn;

    if (!status.loggedIn) {
      userPortalState.features = [];
      userPortalState.defaults = {};
      userPortalState.context = null;
      renderUserStatus();
      const assigned = document.getElementById("userAssignedSegment");
      if (assigned) assigned.innerHTML = "";
      return;
    }

    const profile = await getJson("/api/user/profile");
    userPortalState.features = profile.features || [];
    userPortalState.defaults = profile.defaults || {};
    userPortalState.context = profile.context || {};

    renderUserStatus();
    renderUserDataForm();

    const assigned = document.getElementById("userAssignedSegment");
    const savedProfile = userPortalState.context?.saved_profile;
    if (assigned && savedProfile) {
      assigned.innerHTML = `
        <p><strong>Latest segment:</strong> ${savedProfile.segment_label || "-"}</p>
        <p><strong>Recommendation:</strong> ${savedProfile.recommendation || "-"}</p>
      `;
    }
  } catch (err) {
    setUserMessage(`User portal load failed: ${err.message}`, true);
  }
}

async function handleUserRegister(event) {
  event.preventDefault();
  const full_name =
    document.getElementById("userRegisterName")?.value?.trim() || "";
  const email =
    document.getElementById("userRegisterEmail")?.value?.trim() || "";
  const password = document.getElementById("userRegisterPassword")?.value || "";

  await getJson("/api/user/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_name, email, password }),
  });

  setUserMessage("Registration successful. You are now logged in.");
  await loadUserPortal();
}

async function handleUserLogin(event) {
  event.preventDefault();
  const email = document.getElementById("userLoginEmail")?.value?.trim() || "";
  const password = document.getElementById("userLoginPassword")?.value || "";

  await getJson("/api/user/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  setUserMessage("Login successful.");
  await loadUserPortal();
}

async function handleUserLogout() {
  await getJson("/api/user/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  setUserMessage("Logged out.");
  await loadUserPortal();
}

async function saveUserProfileData() {
  const form = document.getElementById("userDataForm");
  if (!form) return;

  const features = {};
  form.querySelectorAll("input").forEach((input) => {
    const val = Number(input.value);
    features[input.name] = Number.isFinite(val) ? val : 0;
  });

  const res = await getJson("/api/user/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ features }),
  });

  const assigned = document.getElementById("userAssignedSegment");
  if (assigned) {
    const pred = res.prediction || {};
    assigned.innerHTML = `
      <p><strong>Assigned segment:</strong> ${pred.segmentLabel || "-"}</p>
      <p><strong>Recommendation:</strong> ${pred.recommendation || "-"}</p>
      <ul>${(pred.recommendedActions || []).map((a) => `<li>${a}</li>`).join("")}</ul>
    `;
  }

  setUserMessage("User profile saved and segmented.");
  await loadUserPortal();
}

function getDecisionInputs() {
  const budgetEl = document.getElementById("simBudget");
  const growthEl = document.getElementById("simGrowth");
  const retentionEl = document.getElementById("simRetention");

  const budget = Math.max(0, Number(budgetEl?.value || 0));
  const growth = Math.max(0, Math.min(100, Number(growthEl?.value || 0)));
  const retention = Math.max(0, Math.min(100, Number(retentionEl?.value || 0)));
  return { budget, growth, retention };
}

function runDecisionSimulation() {
  const segments = kpiData?.segments || [];
  const { budget, growth, retention } = getDecisionInputs();

  const growthWeight = growth / 100;
  const retentionWeight = retention / 100;

  const scored = segments.map((seg) => {
    const revenueSignal = Math.max(
      0,
      Number(seg.expectedRevenueUpliftPct || 0),
    );
    const retentionSignal = Math.max(0, Number(seg.churnRiskReductionPct || 0));
    const conversionSignal = Math.max(
      0,
      Number(seg.campaignConversionEstimatePct || 0),
    );
    const activityBoost =
      seg.activityLevel === "High"
        ? 1.1
        : seg.activityLevel === "Medium"
          ? 1.0
          : 0.9;

    const composite =
      (revenueSignal * growthWeight * 0.5 +
        retentionSignal * retentionWeight * 0.35 +
        conversionSignal * 0.15) *
      activityBoost;

    return {
      ...seg,
      compositeScore: composite,
    };
  });

  const totalScore = scored.reduce((sum, s) => sum + s.compositeScore, 0) || 1;
  const allocations = scored.map((s) => {
    const allocation = (s.compositeScore / totalScore) * budget;
    const estRevenue =
      allocation *
      (Math.max(0, Number(s.expectedRevenueUpliftPct || 0)) / 100) *
      1.25;
    const estRetained =
      (allocation / 1000) *
      (Math.max(0, Number(s.churnRiskReductionPct || 0)) / 100) *
      3.2;

    return {
      segment: s.segment,
      segmentLabel: s.segmentLabel,
      allocation,
      estRevenue,
      estRetained,
      score: s.compositeScore,
      conversion: Number(s.campaignConversionEstimatePct || 0),
      customers: Number(s.customerCount || 0),
      uplift: Number(s.expectedRevenueUpliftPct || 0),
      churn: Number(s.churnRiskReductionPct || 0),
    };
  });

  const totalRevenue = allocations.reduce((sum, a) => sum + a.estRevenue, 0);
  const retainedCustomers = allocations.reduce(
    (sum, a) => sum + a.estRetained,
    0,
  );
  const avgROI = budget > 0 ? (totalRevenue / budget) * 100 : 0;

  return {
    allocations,
    budget,
    totalRevenue,
    retainedCustomers,
    avgROI,
  };
}

function renderDecisionLiveKpis(simulation) {
  const container = document.getElementById("decisionLiveKpis");
  if (!container) return;

  container.innerHTML = `
    <div class="live-kpi-card">
      <span>Estimated Incremental Revenue</span>
      <strong>$${simulation.totalRevenue.toFixed(0)}</strong>
    </div>
    <div class="live-kpi-card">
      <span>Estimated Customers Retained</span>
      <strong>${simulation.retainedCustomers.toFixed(0)}</strong>
    </div>
    <div class="live-kpi-card">
      <span>Budget Efficiency (ROI Proxy)</span>
      <strong>${simulation.avgROI.toFixed(1)}%</strong>
    </div>
  `;
}

function renderDecisionCharts(simulation) {
  const priorityEl =
    document.getElementById("decisionPriorityChart") ||
    document.getElementById("decisionRiskValueChart");
  const impactEl =
    document.getElementById("decisionImpactChart") ||
    document.getElementById("decisionRevenueContributionChart");
  if (!priorityEl || !impactEl) return;

  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    font: { color: "#16304a", family: "IBM Plex Sans, sans-serif" },
    margin: { t: 36, r: 18, b: 50, l: 52 },
  };
  const config = { responsive: true, displayModeBar: false };

  const alloc = simulation.allocations;
  Plotly.newPlot(
    priorityEl,
    [
      {
        x: alloc.map((a) => a.uplift),
        y: alloc.map((a) => a.churn),
        text: alloc.map((a) => a.segmentLabel),
        mode: "markers+text",
        type: "scatter",
        textposition: "top center",
        marker: {
          size: alloc.map((a) => Math.max(12, a.customers / 45)),
          color: alloc.map((a) => a.conversion),
          colorscale: "Blues",
          showscale: true,
          colorbar: { title: "Conversion" },
          opacity: 0.85,
          line: { width: 1, color: "#0f4c81" },
        },
      },
    ],
    {
      ...layout,
      xaxis: { title: "Revenue Uplift Potential (%)" },
      yaxis: { title: "Churn Reduction Potential (%)" },
    },
    config,
  );

  Plotly.newPlot(
    impactEl,
    [
      {
        x: alloc.map((a) => a.segmentLabel),
        y: alloc.map((a) => a.allocation),
        type: "bar",
        name: "Budget Allocation",
        marker: { color: "#1f6feb" },
      },
      {
        x: alloc.map((a) => a.segmentLabel),
        y: alloc.map((a) => a.estRevenue),
        type: "bar",
        name: "Est. Revenue",
        marker: { color: "#0f766e" },
      },
    ],
    {
      ...layout,
      barmode: "group",
      yaxis: { title: "USD" },
    },
    config,
  );
}

function updateDecisionLab() {
  if (!kpiData?.segments?.length) return;

  const growthEl = document.getElementById("simGrowth");
  const retentionEl = document.getElementById("simRetention");
  const growthValueEl = document.getElementById("simGrowthValue");
  const retentionValueEl = document.getElementById("simRetentionValue");

  if (growthEl && retentionEl && document.activeElement === growthEl) {
    retentionEl.value = String(Math.max(0, 100 - Number(growthEl.value || 0)));
  } else if (
    growthEl &&
    retentionEl &&
    document.activeElement === retentionEl
  ) {
    growthEl.value = String(Math.max(0, 100 - Number(retentionEl.value || 0)));
  }

  const growth = Number(growthEl?.value || 0);
  const retention = Number(retentionEl?.value || 0);
  if (growthValueEl) growthValueEl.textContent = `${growth}%`;
  if (retentionValueEl) retentionValueEl.textContent = `${retention}%`;

  const simulation = runDecisionSimulation();
  renderDecisionLiveKpis(simulation);
  renderDecisionCharts(simulation);
}

function bindDecisionLabControls() {
  const ids = ["simBudget", "simGrowth", "simRetention"];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (!el || el.dataset.bound === "1") return;
    el.addEventListener("input", () => {
      updateDecisionLab();
    });
    el.dataset.bound = "1";
  });
}

function asTable(rows) {
  if (!rows || rows.length === 0) return "<p>No data available.</p>";
  const cols = Object.keys(rows[0]);
  const head = cols.map((c) => `<th>${c}</th>`).join("");
  const body = rows
    .map(
      (r) =>
        `<tr>${cols.map((c) => `<td>${typeof r[c] === "number" ? (r[c].toFixed ? r[c].toFixed(3) : r[c]) : r[c]}</td>`).join("")}</tr>`,
    )
    .join("");
  return `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderKpiLayer(data) {
  const container = document.getElementById("kpiLayer");
  if (!container) return;

  if (!data || (!data.summaryCards?.length && !data.segments?.length)) {
    container.innerHTML = "<p>No KPI data available yet.</p>";
    return;
  }

  const summary = (data.summaryCards || [])
    .map(
      (card) => `
        <div class="kpi-card">
          <span class="kpi-label"><span class="mini-icon">●</span>${card.label}</span>
          <strong class="kpi-value">${card.value}</strong>
        </div>
      `,
    )
    .join("");

  const rows = (data.segments || [])
    .map(
      (item) => `
        <tr>
          <td>${item.segmentLabel || `Segment ${item.segment}`}</td>
          <td>${item.customerCount}</td>
          <td>${item.expectedRevenueUpliftPct}%</td>
          <td>${item.churnRiskReductionPct}%</td>
          <td>${item.campaignConversionEstimatePct}%</td>
        </tr>
      `,
    )
    .join("");

  container.innerHTML = `
    <div class="kpi-summary-grid">${summary}</div>
    <div class="table-wrap kpi-table-wrap">
      <h3>Business KPI Estimates</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>Segment</th>
            <th>Customers</th>
            <th>Revenue Uplift</th>
            <th>Churn Risk Reduction</th>
            <th>Campaign Conversion</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="microcopy">${(data.notes || []).join(" ")}</p>
    </div>
  `;
}

function renderDashboardHighlights(kpis) {
  const container = document.getElementById("dashboardHighlights");
  if (!container) return;

  const top = kpis?.topSegment;
  const risk = kpis?.riskSegment;
  if (!top && !risk) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = `
    <div class="decision-hero-grid">
      <div class="decision-pill success">Top Segment: ${top?.segmentLabel || "-"}</div>
      <div class="decision-pill risk">Risk Segment: ${risk?.segmentLabel || "-"}</div>
    </div>
  `;
}

function renderSegmentSummaryCards(cards) {
  const container = document.getElementById("segmentSummaryCards");
  if (!container) return;

  if (!cards || !cards.length) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = cards
    .map(
      (card) => `
        <article class="segment-summary-card">
          <h3><span class="card-icon">${card.segment === 0 ? "◆" : card.segment === 1 ? "◈" : card.segment === 2 ? "◌" : "▣"}</span>${card.segmentLabel}</h3>
          <p><strong>Customers:</strong> ${card.customerCount}</p>
          <p><strong>Average Spending:</strong> ${card.averageSpending}</p>
          <p><strong>Average Income:</strong> ${card.averageIncome}</p>
          <p><strong>Activity Level:</strong> ${card.activityLevel}</p>
        </article>
      `,
    )
    .join("");
}

function renderActionEngine(kpis) {
  const container = document.getElementById("actionEngine");
  if (!container) return;

  const segments = kpis?.segments || [];
  if (!segments.length) {
    container.innerHTML = "";
    return;
  }

  const blocks = segments
    .map((seg) => {
      const discount = Math.max(
        5,
        Math.round(Number(seg.campaignConversionEstimatePct || 0) / 5) * 5,
      );
      const offerText =
        seg.segmentLabel === "High Value Customers"
          ? "Premium cashback + loyalty upgrade"
          : seg.segmentLabel === "At-Risk Customers"
            ? "Retention discount + fee waiver"
            : seg.segmentLabel === "Potential Customers"
              ? "Starter rewards + spend boost"
              : "Activation bonus + low-friction offer";

      return `
        <div class="action-engine-block">
          <h4><span class="card-icon small">◆</span>${seg.segmentLabel}</h4>
          <p>${seg.recommendationHeadline || "Use focused segment campaigns."}</p>
          <div class="segment-offer-pill">Offer: ${offerText}</div>
          <div class="segment-offer-meta">
            <span>Discount</span>
            <strong>${discount}%</strong>
          </div>
          <ul>
            ${(seg.recommendedActions || []).map((action) => `<li>${action}</li>`).join("")}
          </ul>
        </div>
      `;
    })
    .join("");

  container.innerHTML = `
    <h3>Action Recommendation Engine</h3>
    <div class="action-engine-grid">${blocks}</div>
  `;
}

function renderBusinessDecisionDashboard(data) {
  const hero = document.getElementById("decisionHero");
  const distribution =
    document.getElementById("decisionDistribution") ||
    document.getElementById("decisionOverviewTable");
  const segmentSummaryEl = document.getElementById("decisionSegmentSummary");
  const insightsEl = document.getElementById("decisionInsightsPanel");
  const actions = document.getElementById("decisionActions");
  const finalDecisionEl = document.getElementById("decisionFinalBox");
  const summary =
    document.getElementById("decisionSummary") ||
    document.getElementById("decisionAiSummary") ||
    document.getElementById("decisionFinalSummary");

  if (!hero) return;

  if (!data) {
    hero.innerHTML = "<p>No decision data available.</p>";
    if (distribution) distribution.innerHTML = "";
    if (segmentSummaryEl) segmentSummaryEl.innerHTML = "";
    if (insightsEl) insightsEl.innerHTML = "";
    if (actions) actions.innerHTML = "";
    if (finalDecisionEl) finalDecisionEl.innerHTML = "";
    if (summary) summary.innerHTML = "";
    return;
  }

  hero.innerHTML = `
    <div class="decision-hero-grid">
      <div class="decision-pill success"><span class="card-icon small">◌</span>Most Valuable: ${data.mostValuableSegment?.segmentLabel || "-"}</div>
      <div class="decision-pill risk"><span class="card-icon small">▲</span>Most Risky: ${data.mostRiskySegment?.segmentLabel || "-"}</div>
    </div>
    <div class="decision-path-banner">
      <span>Decision Path</span>
      <strong>Focus the high-value segment, protect the risk segment, and launch the most relevant offer.</strong>
    </div>
  `;

  if (distribution) {
    distribution.innerHTML = asTable(data.segmentDistribution || []);
  }

  if (segmentSummaryEl) {
    segmentSummaryEl.innerHTML = asTable(
      (data.segmentSummary || []).map((item) => ({
        Segment: item.segmentLabel || `Segment ${item.segment}`,
        SharePct: `${Number(item.sharePct || 0).toFixed(2)}%`,
      })),
    );
  }

  if (insightsEl) {
    insightsEl.innerHTML = `<ul>${(data.insightsPanel || [])
      .map((text) => `<li>${text}</li>`)
      .join("")}</ul>`;
  }

  if (actions) {
    actions.innerHTML = (data.suggestedActions || [])
      .map(
        (item) => `
          <div class="decision-action-item">
            <h4>${item.focus} - ${item.segment}</h4>
            <ul>${(item.actions || []).map((a) => `<li>${a}</li>`).join("")}</ul>
          </div>
        `,
      )
      .join("");
  }

  if (summary) {
    summary.innerHTML = `<strong>Auto Insight:</strong> ${data.autoSummary || "No summary available."}`;
  }

  if (finalDecisionEl) {
    const finalDecision = data.finalDecision || {};
    finalDecisionEl.innerHTML = `
      <h3><span class="card-icon">◆</span>Final Decision Box</h3>
      <p><strong>Focus:</strong> ${finalDecision.focus || "-"}</p>
      <p><strong>Risk:</strong> ${finalDecision.risk || "-"}</p>
      <p><strong>Strategy:</strong> ${finalDecision.strategy || "-"}</p>
    `;
  }

  bindDecisionLabControls();
  updateDecisionLab();
}

function renderQualityReport(report, targetId = "qualityReport") {
  const container = document.getElementById(targetId);
  if (!container) return;

  if (!report) {
    container.innerHTML = "<p>No quality report available.</p>";
    return;
  }

  const statusClass = report.readyForRetrain ? "ready" : "warning";
  const warnings = report.warnings || [];
  const critical = report.critical || [];
  const topMissing = report.topMissing || [];
  const outliers = report.outlierFlags || [];
  const schemaGap = report.schemaGap || {};

  container.innerHTML = `
    <div class="quality-status ${statusClass}">
      ${report.readyForRetrain ? "Ready for retraining" : "Review required before retraining"}
    </div>
    <p>Rows: ${report.rows} | Columns: ${report.columns} | Numeric ratio: ${(report.numericRatio * 100).toFixed(1)}% | Missing ratio: ${(report.missingRatio * 100).toFixed(1)}%</p>
    <p><strong>Missing columns:</strong> ${(schemaGap.missingColumns || []).join(", ") || "None"}</p>
    <p><strong>Extra columns:</strong> ${(schemaGap.extraColumns || []).join(", ") || "None"}</p>
    <p><strong>Overlap ratio:</strong> ${schemaGap.overlapRatio ?? "-"}</p>
    ${warnings.length ? `<p><strong>Warnings:</strong></p><ul>${warnings.map((item) => `<li>${item}</li>`).join("")}</ul>` : ""}
    ${critical.length ? `<p><strong>Critical:</strong></p><ul>${critical.map((item) => `<li>${item}</li>`).join("")}</ul>` : ""}
    ${topMissing.length ? `<p><strong>Top missing fields:</strong></p><ul>${topMissing.map((item) => `<li>${item.feature}: ${(item.missingRatio * 100).toFixed(1)}%</li>`).join("")}</ul>` : ""}
    ${outliers.length ? `<p><strong>Outlier flags:</strong></p><ul>${outliers.map((item) => `<li>${item.feature}: ${(item.outlierRatio * 100).toFixed(1)}%</li>`).join("")}</ul>` : ""}
  `;
}

function renderExplainability(explainability) {
  const container = document.getElementById("predictionExplainability");
  if (!container) return;

  if (!explainability) {
    container.innerHTML = "";
    return;
  }

  const rows = (explainability.topFactors || [])
    .map(
      (item) => `
        <tr>
          <td>${item.feature}</td>
          <td>${item.value}</td>
          <td>${item.baseline}</td>
          <td>${item.direction}</td>
          <td>${item.influence}</td>
        </tr>
      `,
    )
    .join("");

  container.innerHTML = `
    <h3>Prediction Explainability</h3>
    <p>${explainability.summary}</p>
    <p><strong>Confidence proxy:</strong> ${explainability.confidenceProxy}</p>
    <table class="data-table">
      <thead>
        <tr>
          <th>Feature</th>
          <th>Value</th>
          <th>Baseline</th>
          <th>Direction</th>
          <th>Influence</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function getJson(url, options = {}) {
  const res = await fetch(url, options);
  const contentType = res.headers.get("content-type") || "";
  const raw = await res.text();

  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    const message =
      data?.error ||
      data?.message ||
      `Request failed (${res.status} ${res.statusText}) for ${url}`;
    throw new Error(message);
  }

  if (!data) {
    const preview = raw.trim().slice(0, 120).replace(/\s+/g, " ");
    throw new Error(
      `Expected JSON from ${url} but received ${contentType || "unknown content type"}. ${preview}`,
    );
  }

  return data;
}

function resolveInitialResult(result, label, fallback = null) {
  if (result.status === "fulfilled") {
    return result.value;
  }

  if (fallback !== null) {
    console.warn(`${label} failed:`, result.reason);
    return fallback;
  }

  throw new Error(
    `${label} failed: ${result.reason?.message || "Unknown error"}`,
  );
}

function renderPredictorForm(meta) {
  const form = document.getElementById("predictForm");
  const hint = document.getElementById("predictorHint");
  form.innerHTML = "";

  if (predictorMode === "advanced") {
    if (hint) {
      hint.textContent =
        "Advanced mode lets you set every model feature for maximum control.";
    }

    meta.features.forEach((feature) => {
      const wrap = document.createElement("div");
      const label = document.createElement("label");
      label.textContent = feature;

      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.name = feature;
      input.value = Number(meta.defaults[feature]).toFixed(4);

      wrap.appendChild(label);
      wrap.appendChild(input);
      form.appendChild(wrap);
    });

    return;
  }

  if (hint) {
    hint.textContent =
      "Quick mode uses Age, Income, and Spending Score. Switch to Advanced mode for all model features.";
  }

  const ageFeature = pickAvailableFeature(meta, demoFeatureMapping.age);
  const incomeFeature = pickAvailableFeature(meta, demoFeatureMapping.income);
  const spendingFeature = pickAvailableFeature(
    meta,
    demoFeatureMapping.spendingScore,
  );

  const demoFields = [
    {
      key: "age",
      label: "Age",
      step: "1",
      value: ageFeature ? Number(meta.defaults[ageFeature]).toFixed(0) : "35",
    },
    {
      key: "income",
      label: "Income",
      step: "any",
      value: incomeFeature
        ? Number(meta.defaults[incomeFeature]).toFixed(2)
        : "5000",
    },
    {
      key: "spendingScore",
      label: "Spending Score",
      step: "any",
      value: spendingFeature
        ? Number(meta.defaults[spendingFeature]).toFixed(2)
        : "50",
    },
  ];

  demoFields.forEach((field) => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.textContent = field.label;

    const input = document.createElement("input");
    input.type = "number";
    input.step = field.step;
    input.name = field.key;
    input.value = field.value;

    wrap.appendChild(label);
    wrap.appendChild(input);
    form.appendChild(wrap);
  });
}

function setPredictorMode(mode) {
  predictorMode = mode;

  const quickBtn = document.getElementById("quickModeBtn");
  const advancedBtn = document.getElementById("advancedModeBtn");

  if (quickBtn && advancedBtn) {
    quickBtn.classList.toggle("active", mode === "quick");
    advancedBtn.classList.toggle("active", mode === "advanced");
  }

  if (featureMeta.features.length) {
    renderPredictorForm(featureMeta);
  }
}

function renderQuickMetrics(metrics) {
  const quick = document.getElementById("quickMetrics");
  quick.innerHTML = "";
  const items = [
    `Selected k: ${metrics.selected_k ?? "-"}`,
    `Best k (Silhouette): ${metrics.best_k_by_silhouette ?? "-"}`,
    `Silhouette: ${(metrics.final_silhouette ?? 0).toFixed(4)}`,
    `RF Accuracy: ${(metrics.rf_accuracy ?? 0).toFixed(4)}`,
  ];
  items.forEach((item) => {
    const pill = document.createElement("div");
    pill.className = "metric-pill";
    pill.textContent = item;
    quick.appendChild(pill);
  });
}

function renderDashboard(data) {
  dashboardData = data;

  renderQuickMetrics(data.metrics || {});

  renderDashboardSummary(data);
  renderSegmentSummaryCards(data.segmentSummaryCards || []);

  if (document.getElementById("dashboard")?.classList.contains("active")) {
    renderDashboardCharts(data);
  }
}

function renderDashboardSummary(data) {
  const insightsEl = document.getElementById("insights");
  insightsEl.innerHTML = "";
  (data.insights || []).forEach((insight) => {
    const div = document.createElement("div");
    div.className = "insight-item";
    div.textContent = insight;
    insightsEl.appendChild(div);
  });

  const profileRows = (data.segmentProfile || []).map((row) => {
    const segId = Number(row.Segment);
    return {
      Segment: segId,
      SegmentLabel: data.segmentNames?.[segId] || `Segment ${segId}`,
      ...row,
    };
  });

  document.getElementById("profileTable").innerHTML = asTable(profileRows);
}

function renderCorrelationHeatmap(data) {
  const container = document.getElementById("heatmapChart");
  if (!container) return;

  const columns = data.correlation.columns || [];
  const matrix = data.correlation.matrix || [];
  if (!columns.length || !matrix.length) {
    container.innerHTML = "<p>No correlation data available.</p>";
    return;
  }
  container.innerHTML = "";
  Plotly.newPlot(
    container,
    [
      {
        z: matrix,
        x: columns,
        y: columns,
        type: "heatmap",
        colorscale: "RdBu",
        zmin: -1,
        zmax: 1,
        reversescale: true,
        hovertemplate: "%{y} vs %{x}<br>Correlation: %{z:.3f}<extra></extra>",
      },
    ],
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "#ffffff",
      font: { color: "#16304a", family: "IBM Plex Sans, sans-serif" },
      margin: { t: 44, r: 20, b: 92, l: 110 },
      xaxis: {
        tickangle: -35,
        automargin: true,
      },
      yaxis: {
        automargin: true,
      },
    },
    { responsive: true, displayModeBar: false },
  );
}

function renderFeatureImportanceBars(data) {
  const container = document.getElementById("importanceChart");
  if (!container) return;

  const features = data.featureImportance.features || [];
  const scores = data.featureImportance.scores || [];
  if (!features.length || !scores.length) {
    container.innerHTML = "<p>No feature importance data available.</p>";
    return;
  }

  const maxScore = Math.max(...scores, 1);
  const rows = features
    .map((feature, index) => {
      const score = Number(scores[index] || 0);
      const width = Math.max(6, (score / maxScore) * 100);
      const pct = score * 100;
      return `
        <div class="importance-row">
          <div class="importance-label">${feature}</div>
          <div class="importance-track"><div class="importance-fill" style="width:${width}%;"></div></div>
          <div class="importance-score">${pct.toFixed(1)}%</div>
        </div>
      `;
    })
    .join("");

  container.innerHTML = `<div class="importance-list">${rows}</div>`;
}

function renderDashboardCharts(data) {
  const chartLayout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    font: { color: "#16304a", family: "IBM Plex Sans, sans-serif" },
    margin: { t: 52, r: 24, b: 96, l: 56 },
    colorway: ["#1f6feb", "#0ea5e9", "#2563eb", "#0f766e", "#ef4444"],
    xaxis: {
      gridcolor: "rgba(22, 48, 74, 0.08)",
      zerolinecolor: "rgba(22, 48, 74, 0.12)",
      linecolor: "rgba(22, 48, 74, 0.18)",
      automargin: true,
      tickfont: { color: "#16304a" },
    },
    yaxis: {
      gridcolor: "rgba(22, 48, 74, 0.08)",
      zerolinecolor: "rgba(22, 48, 74, 0.12)",
      linecolor: "rgba(22, 48, 74, 0.18)",
      automargin: true,
      tickfont: { color: "#16304a" },
    },
    legend: {
      orientation: "h",
      yanchor: "top",
      y: -0.22,
      xanchor: "left",
      x: 0,
      font: { color: "#16304a" },
    },
    title: { font: { color: "#16304a" } },
  };

  const plotConfig = { responsive: true, displayModeBar: false };
  const ensureChart = (containerId, plotData, layout) => {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = "";
    Plotly.newPlot(container, plotData, layout, plotConfig);
  };

  const pcaPoints = data.pcaScatter.points;
  const segmentNames = data.segmentNames || {};
  const segments = [...new Set(pcaPoints.map((p) => p.Segment))].sort(
    (a, b) => a - b,
  );
  const pcaTraces = segments.map((seg) => {
    const pts = pcaPoints.filter((p) => p.Segment === seg);
    return {
      x: pts.map((p) => p.PC1),
      y: pts.map((p) => p.PC2),
      mode: "markers",
      name: segmentNames[seg] || `Segment ${seg}`,
      type: "scatter",
      marker: { size: 8, opacity: 0.8 },
    };
  });

  ensureChart("pcaChart", pcaTraces, {
    ...chartLayout,
    title: {
      text: `PCA Scatter - Explained Variance ${(data.pcaScatter.explainedVariance2D * 100).toFixed(1)}%`,
      pad: { b: 10 },
    },
    xaxis: { ...chartLayout.xaxis, title: "PC1" },
    yaxis: { ...chartLayout.yaxis, title: "PC2" },
  });

  ensureChart(
    "segmentChart",
    [
      {
        x:
          data.segmentDistribution.labels ||
          data.segmentDistribution.segments.map((s) => `Segment ${s}`),
        y: data.segmentDistribution.counts,
        type: "bar",
        marker: { color: "#1f6feb" },
      },
    ],
    { ...chartLayout, title: "Customer Distribution" },
  );

  ensureChart(
    "segmentPieChart",
    [
      {
        labels:
          data.segmentDistribution.labels ||
          data.segmentDistribution.segments.map((s) => `Segment ${s}`),
        values: data.segmentDistribution.counts,
        type: "pie",
        hole: 0.42,
        textinfo: "label+percent",
        marker: {
          colors: ["#0f766e", "#1f6feb", "#0ea5e9", "#2563eb", "#ef4444"],
        },
      },
    ],
    {
      ...chartLayout,
      title: "Segment Mix",
      margin: { t: 48, r: 24, b: 40, l: 24 },
      showlegend: false,
    },
  );

  renderCorrelationHeatmap(data);
  renderFeatureImportanceBars(data);
}

async function loadInitial() {
  const [
    featuresResult,
    dashboardResult,
    fitResult,
    novaStatusResult,
    kpisResult,
    qualityResult,
    decisionDashboardResult,
  ] = await Promise.allSettled([
    getJson("/api/features"),
    getJson("/api/dashboard"),
    getJson("/api/algorithm-fit"),
    getJson("/api/nova-status"),
    getJson("/api/kpis"),
    getJson("/api/admin/data-quality"),
    getJson("/api/business-dashboard"),
  ]);

  const features = resolveInitialResult(featuresResult, "Features API");
  const dashboard = resolveInitialResult(dashboardResult, "Dashboard API");
  const fit = resolveInitialResult(fitResult, "Algorithm fit API");
  const novaStatus = resolveInitialResult(novaStatusResult, "NOVA status API", {
    active: false,
    label: "Unavailable",
    description: "NOVA status endpoint is not reachable.",
  });
  const kpis = resolveInitialResult(kpisResult, "KPI API", {
    summaryCards: [],
    segments: [],
    notes: ["KPI data is currently unavailable."],
  });
  const quality = resolveInitialResult(qualityResult, "Data quality API", {
    readyForRetrain: false,
    warnings: ["Data quality endpoint is not reachable."],
    criticalIssues: [],
    topMissingColumns: [],
    outlierSignals: [],
  });
  const decisionDashboard = resolveInitialResult(
    decisionDashboardResult,
    "Business decision API",
    {
      hero: null,
      notes: ["Business decision dashboard is unavailable."],
      segments: [],
      actions: [],
    },
  );

  featureMeta = features;
  renderPredictorForm(features);
  renderDashboard(dashboard);
  renderNovaStatus(novaStatus);
  kpiData = kpis;
  renderKpiLayer(kpis);
  renderDashboardHighlights(kpis);
  renderActionEngine(kpis);
  renderQualityReport(quality);
  await loadAdminUserInsights();
  businessDecisionData = decisionDashboard;
  if (document.getElementById("decisions")?.classList.contains("active")) {
    renderBusinessDecisionDashboard(decisionDashboard);
  }

  const fitDiv = document.getElementById("fitSummary");
  fitDiv.innerHTML = `
    <h3>Dataset-Algorithm Fit</h3>
    <p>Rows: ${fit.rows} | Columns: ${fit.columns} | Numeric ratio: ${(fit.numericRatio * 100).toFixed(1)}%</p>
    <ul>${fit.recommendedAlgorithms.map((rec) => `<li>${rec}</li>`).join("")}</ul>
  `;
}

function renderAdminUserInsights(data) {
  const cardsEl = document.getElementById("adminUserOverviewCards");
  const newUsersEl = document.getElementById("adminNewUsersTable");
  const returningUsersEl = document.getElementById("adminReturningUsersTable");
  const usersTableEl = document.getElementById("adminUsersUsageTable");
  const logsTableEl = document.getElementById("adminCampaignLogsTable");
  const segmentSelect = document.getElementById("campaignSegment");
  if (
    !cardsEl ||
    !newUsersEl ||
    !returningUsersEl ||
    !usersTableEl ||
    !logsTableEl ||
    !segmentSelect
  )
    return;

  const summary = data.summary || {};
  cardsEl.innerHTML = `
    <div class="kpi-summary-grid">
      <div class="kpi-card"><span class="kpi-label">Total Users</span><strong class="kpi-value">${summary.totalUsers ?? 0}</strong></div>
      <div class="kpi-card"><span class="kpi-label">New Users</span><strong class="kpi-value">${summary.newUsers ?? 0}</strong></div>
      <div class="kpi-card"><span class="kpi-label">Returning Users</span><strong class="kpi-value">${summary.returningUsers ?? 0}</strong></div>
    </div>
  `;

  const userRows = (data.users || []).map((row) => ({
    Email: row.email,
    Name: row.full_name,
    Segment: row.segment_label,
    LoginCount: row.login_count,
    Purchases: row.avg_purchases,
    PurchasesTrx: row.purchases_trx,
    CashAdvance: row.cash_advance,
    Updated: row.updated_at,
  }));

  const newUsersRows = (data.users || [])
    .filter((row) => Number(row.login_count || 0) <= 1)
    .map((row) => ({
      Email: row.email,
      Name: row.full_name,
      Segment: row.segment_label,
      LoginCount: row.login_count,
      Updated: row.updated_at,
    }));

  const returningUsersRows = (data.users || [])
    .filter((row) => Number(row.login_count || 0) > 1)
    .map((row) => ({
      Email: row.email,
      Name: row.full_name,
      Segment: row.segment_label,
      LoginCount: row.login_count,
      Updated: row.updated_at,
    }));

  newUsersEl.innerHTML = asTable(newUsersRows);
  returningUsersEl.innerHTML = asTable(returningUsersRows);
  usersTableEl.innerHTML = asTable(userRows);

  const logRows = (data.campaignLogs || []).map((row) => ({
    Segment: row.segment_label,
    Subject: row.subject,
    Offer: row.offer_text,
    DiscountPct: row.discount_pct,
    Recipients: row.recipient_count,
    SentAt: row.created_at,
  }));
  logsTableEl.innerHTML = asTable(logRows);

  const current = segmentSelect.value;
  segmentSelect.innerHTML = `<option value="">All Segments</option>`;
  (data.segmentOptions || []).forEach((seg) => {
    const opt = document.createElement("option");
    opt.value = seg;
    opt.textContent = seg;
    segmentSelect.appendChild(opt);
  });
  if (["", ...(data.segmentOptions || [])].includes(current)) {
    segmentSelect.value = current;
  }
}

async function loadAdminUserInsights() {
  try {
    const data = await getJson("/api/admin/user-insights");
    renderAdminUserInsights(data);
  } catch (err) {
    const adminResult = document.getElementById("adminResult");
    if (adminResult) {
      adminResult.textContent = `Admin user insights load failed: ${err.message}`;
    }
  }
}

async function sendAdminCampaign(event) {
  event.preventDefault();
  const segment_label = document.getElementById("campaignSegment")?.value || "";
  const subject =
    document.getElementById("campaignSubject")?.value?.trim() || "";
  const offer_text =
    document.getElementById("campaignOffer")?.value?.trim() || "";
  const discount_pct = Number(
    document.getElementById("campaignDiscount")?.value || 0,
  );

  const res = await getJson("/api/admin/send-campaign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_label, subject, offer_text, discount_pct }),
  });

  const resultEl = document.getElementById("adminCampaignResult");
  if (resultEl) resultEl.textContent = res.message;
  await loadAdminUserInsights();
}

async function handleUserSimulatorPredict() {
  const ageInput = Number(document.getElementById("userSimAge")?.value || 0);
  const incomeInput = Number(
    document.getElementById("userSimIncome")?.value || 0,
  );
  const spendingInput = Number(
    document.getElementById("userSimSpendingScore")?.value || 0,
  );
  const txnInput = Number(
    document.getElementById("userSimTxnFrequency")?.value || 0,
  );

  const features = { ...featureMeta.defaults };

  const ageFeature = pickAvailableFeature(featureMeta, demoFeatureMapping.age);
  const incomeFeature = pickAvailableFeature(
    featureMeta,
    demoFeatureMapping.income,
  );
  const spendingFeature = pickAvailableFeature(
    featureMeta,
    demoFeatureMapping.spendingScore,
  );
  const txnFeature = pickAvailableFeature(
    featureMeta,
    demoFeatureMapping.transactionFrequency,
  );

  if (ageFeature && Number.isFinite(ageInput)) features[ageFeature] = ageInput;
  if (incomeFeature && Number.isFinite(incomeInput))
    features[incomeFeature] = incomeInput;
  if (spendingFeature && Number.isFinite(spendingInput))
    features[spendingFeature] = spendingInput;
  if (txnFeature && Number.isFinite(txnInput)) features[txnFeature] = txnInput;

  const result = await getJson("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ features }),
  });

  const simResult = document.getElementById("userSimResult");
  if (!simResult) return;

  const segmentName = result.segmentName || "High Value Customer";
  const recommendation =
    result.recommendation || "You may receive premium offers";

  let insight = "You are a frequent spender";
  if (txnInput < 25 || spendingInput < 40) {
    insight = "Your spending activity is moderate and can be improved";
  }

  const premiumTier =
    spendingInput >= 75 && txnInput >= 60
      ? "Platinum"
      : spendingInput >= 55 && txnInput >= 40
        ? "Gold"
        : "Silver";

  const baseDiscount =
    premiumTier === "Platinum" ? 20 : premiumTier === "Gold" ? 15 : 10;

  const loyaltyPoints = Math.max(
    100,
    Math.round((spendingInput * 12 + txnInput * 8 + incomeInput / 100) * 0.6),
  );

  const accountBalance = Math.max(1200, Math.round(incomeInput * 1.65));
  const availableLimit = Math.max(5000, Math.round(incomeInput * 2.25));

  const offerCards = [
    {
      title: "Cashback Booster",
      value: `${baseDiscount}% cashback`,
      detail: "On groceries, fuel, utility bills, and card swipes.",
    },
    {
      title: "Premium Lifestyle",
      value: "Dining + Travel",
      detail: `Best for ${segmentName} users with higher card activity.`,
    },
    {
      title: "Rewards Wallet",
      value: `${loyaltyPoints.toLocaleString()} points`,
      detail: "Reward estimate based on spending and transaction frequency.",
    },
  ];

  simResult.innerHTML = `
    <div class="sim-bank-shell">
      <div class="sim-bank-top">
        <div>
          <p class="sim-kicker">SegmentIQ Banking Preview</p>
          <div class="sim-segment">${segmentName}</div>
          <p class="microcopy">${insight}</p>
        </div>
        <div class="sim-badge">Tier ${premiumTier}</div>
      </div>

      <div class="sim-bank-card">
        <div class="sim-card-glow"></div>
        <div class="sim-card-row">
          <div>
            <p class="sim-card-label">Available Limit</p>
            <h3>$${availableLimit.toLocaleString()}</h3>
          </div>
          <div>
            <p class="sim-card-label">Reward Balance</p>
            <h3>${loyaltyPoints.toLocaleString()}</h3>
          </div>
        </div>
        <div class="sim-card-row bottom">
          <span>Monthly Spend Signal</span>
          <strong>${spendingInput.toFixed(0)} / 100</strong>
        </div>
      </div>

      <div class="sim-meta-grid">
        <div class="sim-meta-item">
          <span>Offer Recommendation</span>
          <strong>${recommendation}</strong>
        </div>
        <div class="sim-meta-item">
          <span>Eligible Discount</span>
          <strong>${baseDiscount}% cashback</strong>
        </div>
      </div>

      <div class="sim-offers-grid">
        ${offerCards
          .map(
            (card) => `
              <article class="sim-offer-card">
                <h4>${card.title}</h4>
                <p class="offer-value">${card.value}</p>
                <p>${card.detail}</p>
              </article>
            `,
          )
          .join("")}
      </div>

      <div class="sim-next-action">
        <div>
          <p class="sim-card-label">Next Best Action</p>
          <strong>Target ${segmentName} with ${baseDiscount}% premium offer</strong>
        </div>
        <button class="primary" type="button">Claim Offer</button>
      </div>
    </div>
  `;
}

async function handlePredict() {
  const features = { ...featureMeta.defaults };

  if (predictorMode === "advanced") {
    const inputs = document.querySelectorAll("#predictForm input");
    inputs.forEach((input) => {
      const value = Number(input.value);
      if (Number.isFinite(value)) {
        features[input.name] = value;
      }
    });
  } else {
    const ageInput = Number(
      document.querySelector('#predictForm input[name="age"]').value,
    );
    const incomeInput = Number(
      document.querySelector('#predictForm input[name="income"]').value,
    );
    const spendingInput = Number(
      document.querySelector('#predictForm input[name="spendingScore"]').value,
    );

    const ageFeature = pickAvailableFeature(
      featureMeta,
      demoFeatureMapping.age,
    );
    const incomeFeature = pickAvailableFeature(
      featureMeta,
      demoFeatureMapping.income,
    );
    const spendingFeature = pickAvailableFeature(
      featureMeta,
      demoFeatureMapping.spendingScore,
    );

    if (ageFeature && Number.isFinite(ageInput))
      features[ageFeature] = ageInput;
    if (incomeFeature && Number.isFinite(incomeInput))
      features[incomeFeature] = incomeInput;
    if (spendingFeature && Number.isFinite(spendingInput))
      features[spendingFeature] = spendingInput;
  }

  const result = await getJson("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ features }),
  });

  document.getElementById("predictResult").innerHTML = `
    <h3>${result.message}</h3>
    <p><strong>Recommendation:</strong> ${result.recommendation}</p>
    <ul>${(result.recommendedActions || []).map((action) => `<li>${action}</li>`).join("")}</ul>
    <p>Model agreement: RF = ${result.segmentName}, KMeans = Segment ${result.kmeansSegment}</p>
    <div class="decision-path-banner">
      <span>Decision</span>
      <strong>Use ${result.segmentName || "this segment"} for the next campaign and follow the recommended actions.</strong>
    </div>
  `;
  renderExplainability(result.explainability);
}

function addChat(role, text) {
  const windowEl = document.getElementById("chatWindow");
  const msg = document.createElement("div");
  msg.className = `chat-msg ${role}`;
  msg.textContent = text;
  windowEl.appendChild(msg);
  windowEl.scrollTop = windowEl.scrollHeight;
}

function loadNovaChatHistory() {
  try {
    const raw = localStorage.getItem(NOVA_CHAT_HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    novaChatHistory = Array.isArray(parsed) ? parsed : [];
  } catch {
    novaChatHistory = [];
  }
  renderNovaChatHistory();
}

function persistNovaChatHistory() {
  localStorage.setItem(NOVA_CHAT_HISTORY_KEY, JSON.stringify(novaChatHistory));
}

function renderNovaChatHistory() {
  const historyEl = document.getElementById("chatHistory");
  if (!historyEl) return;

  if (!novaChatHistory.length) {
    historyEl.innerHTML = "<p>No chat history yet.</p>";
    return;
  }

  const rows = novaChatHistory
    .slice()
    .reverse()
    .map((item) => ({
      Time: item.time,
      Question: item.question,
      Answer: item.answer,
    }));

  historyEl.innerHTML = asTable(rows);
}

function appendNovaChatHistory(question, answer) {
  const now = new Date();
  novaChatHistory.push({
    time: now.toLocaleString(),
    question,
    answer,
  });
  if (novaChatHistory.length > 50) {
    novaChatHistory = novaChatHistory.slice(-50);
  }
  persistNovaChatHistory();
  renderNovaChatHistory();
}

function clearNovaChatHistory() {
  novaChatHistory = [];
  persistNovaChatHistory();
  renderNovaChatHistory();
}

async function sendChat(question) {
  addChat("user", question);
  const res = await getJson("/api/nova-chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  addChat("nova", res.answer);
  appendNovaChatHistory(question, res.answer || "");
}

async function uploadDataset() {
  const fileInput = document.getElementById("datasetFile");
  if (!fileInput.files.length) throw new Error("Select a CSV file first.");
  const fd = new FormData();
  fd.append("dataset", fileInput.files[0]);

  const res = await fetch("/api/admin/upload", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || data.message || "Upload failed");
  document.getElementById("adminResult").textContent = data.message;
  renderQualityReport(data.qualityReport);
}

async function retrainModels() {
  const k = Number(document.getElementById("businessK").value || 4);
  const result = await getJson("/api/admin/retrain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ business_k: k }),
  });

  document.getElementById("adminResult").innerHTML =
    `<p>${result.message} RF Accuracy: ${result.summary.rf_accuracy.toFixed(4)}, Silhouette: ${result.summary.final_silhouette.toFixed(4)}</p>`;

  const insightEl = document.getElementById("retrainInsights");
  const retrainInsights = result.retrainInsights || {};
  insightEl.innerHTML = `
    <h3>Retraining Insights</h3>
    <p>${(retrainInsights.notes || []).join(" ")}</p>
    <h4>New Segment Distribution</h4>
    ${asTable(retrainInsights.segmentDistribution || [])}
    <h4>Pattern Changes</h4>
    ${asTable(retrainInsights.patternChanges || [])}
  `;

  await loadInitial();
}

document.getElementById("predictBtn").addEventListener("click", async () => {
  try {
    await handlePredict();
  } catch (err) {
    document.getElementById("predictResult").textContent = err.message;
  }
});

document.getElementById("chatSendBtn").addEventListener("click", async () => {
  const input = document.getElementById("chatInput");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  try {
    await sendChat(q);
  } catch (err) {
    addChat("nova", `Error: ${err.message}`);
  }
});

document.querySelectorAll(".quick-actions .ghost").forEach((btn) => {
  btn.addEventListener("click", async () => {
    try {
      await sendChat(btn.dataset.question);
    } catch (err) {
      addChat("nova", `Error: ${err.message}`);
      appendNovaChatHistory(
        btn.dataset.question || "",
        `Error: ${err.message}`,
      );
    }
  });
});

document
  .getElementById("clearChatHistoryBtn")
  ?.addEventListener("click", () => {
    clearNovaChatHistory();
  });

document.getElementById("quickModeBtn")?.addEventListener("click", () => {
  setPredictorMode("quick");
});

document.getElementById("advancedModeBtn")?.addEventListener("click", () => {
  setPredictorMode("advanced");
});

document.getElementById("uploadBtn").addEventListener("click", async () => {
  try {
    await uploadDataset();
  } catch (err) {
    document.getElementById("adminResult").textContent = err.message;
  }
});

document.getElementById("retrainBtn").addEventListener("click", async () => {
  try {
    await retrainModels();
  } catch (err) {
    document.getElementById("adminResult").textContent = err.message;
  }
});

document
  .getElementById("userSimPredictBtn")
  ?.addEventListener("click", async () => {
    try {
      await handleUserSimulatorPredict();
    } catch (err) {
      const simResult = document.getElementById("userSimResult");
      if (simResult) simResult.textContent = err.message;
    }
  });

document
  .getElementById("adminCampaignForm")
  ?.addEventListener("submit", async (event) => {
    try {
      await sendAdminCampaign(event);
    } catch (err) {
      const resultEl = document.getElementById("adminCampaignResult");
      if (resultEl) resultEl.textContent = err.message;
    }
  });

document
  .getElementById("userRegisterForm")
  ?.addEventListener("submit", async (event) => {
    try {
      await handleUserRegister(event);
    } catch (err) {
      setUserMessage(err.message, true);
    }
  });

document
  .getElementById("userLoginForm")
  ?.addEventListener("submit", async (event) => {
    try {
      await handleUserLogin(event);
    } catch (err) {
      setUserMessage(err.message, true);
    }
  });

document
  .getElementById("userLogoutBtn")
  ?.addEventListener("click", async () => {
    try {
      await handleUserLogout();
    } catch (err) {
      setUserMessage(err.message, true);
    }
  });

document
  .getElementById("userSaveProfileBtn")
  ?.addEventListener("click", async () => {
    try {
      await saveUserProfileData();
    } catch (err) {
      setUserMessage(err.message, true);
    }
  });

loadInitial().catch((err) => {
  document.getElementById("fitSummary").textContent =
    `Failed to load dashboard: ${err.message}`;
});

loadNovaChatHistory();
