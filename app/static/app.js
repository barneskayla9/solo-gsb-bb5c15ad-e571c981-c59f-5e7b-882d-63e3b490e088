/* 科研配方量纲校验台 —— 前端逻辑（原生 JS，无依赖） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const exprEl = $("#expr");
const targetEl = $("#target");
const varsEl = $("#vars");
const resultEl = $("#result");

let lastExport = null;

/* ---------------- 工具 ---------------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmt(x) {
  if (typeof x !== "number") return String(x);
  if (x === 0) return "0";
  if (!isFinite(x)) return String(x);
  const a = Math.abs(x);
  if (a >= 1e12 || a < 1e-9) return x.toExponential(6);
  return String(Number(x.toPrecision(12)));
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json();
}

function download(filename, text) {
  const blob = new Blob([text], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/* ---------------- 变量编辑 ---------------- */

function addVarRow(name = "", expr = "", unc = "") {
  const row = document.createElement("div");
  row.className = "var-row";
  row.innerHTML =
    `<input class="mono vname" placeholder="名称" value="${escapeHtml(name)}" spellcheck="false">` +
    `<span class="eq">=</span>` +
    `<input class="mono vexpr" placeholder="取值表达式，如 3 m/s" value="${escapeHtml(expr)}" spellcheck="false">` +
    `<span class="u-label" title="该直接测量量的标准不确定度（带单位）">u</span>` +
    `<input class="mono vunc" placeholder="标准不确定度，如 0.1 m/s（可留空）" value="${escapeHtml(unc)}" spellcheck="false">` +
    `<button class="btn small danger vdel" title="删除该变量">✕</button>`;
  row.querySelector(".vdel").addEventListener("click", () => row.remove());
  varsEl.appendChild(row);
}

function collectVariables() {
  const vars = {};
  varsEl.querySelectorAll(".var-row").forEach((row) => {
    const name = row.querySelector(".vname").value.trim();
    const expr = row.querySelector(".vexpr").value;
    if (name) vars[name] = expr;
  });
  return vars;
}

function collectUncertainties() {
  const uncs = {};
  varsEl.querySelectorAll(".var-row").forEach((row) => {
    const name = row.querySelector(".vname").value.trim();
    const unc = row.querySelector(".vunc").value.trim();
    if (name && unc) uncs[name] = unc;
  });
  return uncs;
}

/* ---------------- 校验与渲染 ---------------- */

async function runCheck() {
  const body = {
    expression: exprEl.value,
    variables: collectVariables(),
    uncertainties: collectUncertainties(),
    target_unit: targetEl.value || null,
  };
  let res;
  try {
    res = await api("/api/check", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    resultEl.innerHTML = `<div class="status bad">请求失败：${escapeHtml(err.message)}</div>`;
    return;
  }
  lastExport = { app: "dimcheck", kind: "check-result", request: body, response: res };
  $("#exportResult").disabled = false;
  renderResult(res, body);
}

function sourceOf(err, body) {
  if (err.source === "expression") return ["表达式", body.expression];
  if (err.source === "target") return ["目标单位", body.target_unit || ""];
  if (err.source && err.source.startsWith("var:")) {
    const name = err.source.slice(4);
    return [`变量 ${name}`, (body.variables || {})[name] ?? ""];
  }
  if (err.source && err.source.startsWith("unc:")) {
    const name = err.source.slice(4);
    return [`变量 ${name} 的标准不确定度`, (body.uncertainties || {})[name] ?? ""];
  }
  return [err.source, ""];
}

function fragmentHtml(text, start, end) {
  start = Math.max(0, Math.min(start, text.length));
  end = Math.max(start, Math.min(end, text.length));
  const frag = text.slice(start, end);
  return (
    escapeHtml(text.slice(0, start)) +
    `<mark>${escapeHtml(frag) || "▸"}</mark>` +
    escapeHtml(text.slice(end))
  );
}

function renderResult(res, body) {
  const parts = [];
  const errors = res.errors || [];

  if (errors.length === 0 && res.result) {
    const uNote = res.result.uncertainty ? "，已给出合成标准不确定度" : "";
    parts.push(`<div class="status ok">✓ 校验通过，量纲一致${uNote}</div>`);
  } else if (errors.length > 0) {
    parts.push(`<div class="status bad">✗ 发现 ${errors.length} 个错误</div>`);
  }

  for (const w of res.warnings || []) {
    parts.push(`<div class="status warn">⚠ ${escapeHtml(w.message)}</div>`);
  }

  for (const err of errors) {
    const [label, text] = sourceOf(err, body);
    const frag = text.slice(err.start, err.end);
    parts.push(`
      <div class="error-item">
        <div class="error-head">
          <span class="tag">${escapeHtml(label)}</span>
          <span class="kind">${escapeHtml(err.kind)}</span>
          <span>${escapeHtml(err.message)}</span>
        </div>
        <div class="code">${fragmentHtml(text, err.start, err.end)}</div>
        ${frag ? `<div class="muted small-text">出错片段：<code>${escapeHtml(frag)}</code>（位置 ${err.start}–${err.end}）</div>` : ""}
      </div>`);
  }

  if (res.result) {
    const r = res.result;
    const vec = r.dimension
      .map((e, i) => (e !== "0" ? `${r.dimension_names[i]} = ${e}` : null))
      .filter(Boolean)
      .join("，") || "全部为零（无量纲）";
    const derived = r.derived.length
      ? r.derived.map((d) => `<span class="chip">${escapeHtml(d)}</span>`).join(" ")
      : '<span class="muted">无</span>';

    const u = r.uncertainty;
    let targetRow = "";
    let uncRows = "";
    if (r.target) {
      const tv = `${fmt(r.target.value)} ${escapeHtml(r.target.unit)}`;
      const tu = r.target.uncertainty != null
        ? ` ± <span class="unc-val mono">${fmt(r.target.uncertainty)}</span> ${escapeHtml(r.target.unit)}`
        : "";
      targetRow =
        `<tr><th>换算结果</th><td class="mono strong">${tv}${tu}</td></tr>`;
    }
    if (u) {
      const rel = u.relative != null
        ? ` <span class="muted">（相对不确定度 ${(u.relative * 100).toPrecision(3)}%）</span>`
        : "";
      uncRows +=
        `<tr><th>合成标准不确定度 u<sub>c</sub></th>` +
        `<td class="mono strong"><span class="unc-val">±${fmt(u.si_value)}</span> ${escapeHtml(u.si_unit || "1")}${rel}</td></tr>`;
      if (u.sources.length) {
        const srcRows = u.sources
          .map((s) => {
            const cr = s.contribution_si / u.si_value;
            const pct = u.si_value > 0 ? (cr * 100).toPrecision(3) : "0";
            return `<tr>
              <td class="mono">${escapeHtml(s.name)}</td>
              <td class="mono">${fmt(s.nominal_si)}</td>
              <td class="mono">±${fmt(s.uncertainty_si)}</td>
              <td class="mono">${s.step_si ? `h=${fmt(s.step_si)}` : "—"}</td>
              <td class="mono">±${fmt(s.contribution_si)}</td>
              <td class="muted mono">${pct}%</td>
            </tr>`;
          })
          .join("");
        uncRows +=
          `<tr><th>扰动源分解</th><td>
            <div class="muted small-text">双侧中心差分（步长因子 ${u.step_factor}），各独立测量输入的贡献与平方和占比：</div>
            <table class="unc-table">
              <thead><tr><th>输入</th><th>标称值(SI)</th><th>标准不确定度(SI)</th><th>步长(SI)</th><th>对结果的贡献</th><th>占比</th></tr></thead>
              <tbody>${srcRows}</tbody>
            </table>
          </td></tr>`;
        const varRows = u.variables
          .map((v) => `<tr>
            <td class="mono">${escapeHtml(v.name)}</td>
            <td class="mono">${fmt(v.si_value)} ${escapeHtml(v.si_unit || "1")}</td>
            <td class="mono">±${fmt(v.uncertainty_si)} ${escapeHtml(v.si_unit || "1")}</td>
          </tr>`)
          .join("");
        uncRows +=
          `<tr><th>变量链传播</th><td>
            <div class="muted small-text">经完整变量链传播后每个变量的标称值与合成标准不确定度（SI）：</div>
            <table class="unc-table">
              <thead><tr><th>变量</th><th>标称值</th><th>合成标准不确定度</th></tr></thead>
              <tbody>${varRows}</tbody>
            </table>
          </td></tr>`;
      }
    }

    parts.push(`
      <table class="result-table">
        <tr><th>量纲</th><td class="mono strong">${escapeHtml(r.dimension_str)}</td></tr>
        <tr><th>量纲向量</th><td class="mono">${escapeHtml(vec)}</td></tr>
        <tr><th>SI 数值</th><td class="mono strong">${fmt(r.si_value)} ${escapeHtml(r.si_unit)}</td></tr>
        ${uncRows}
        <tr><th>等价单位</th><td>${derived}</td></tr>
        ${targetRow}
      </table>`);
  }

  resultEl.innerHTML = parts.join("");
}

/* ---------------- 案例管理 ---------------- */

async function refreshCases() {
  const ul = $("#caseList");
  try {
    const { cases } = await api("/api/cases");
    ul.innerHTML = "";
    if (!cases.length) {
      ul.innerHTML = `<li class="muted">暂无保存的案例</li>`;
      return;
    }
    for (const c of cases) {
      const li = document.createElement("li");
      li.innerHTML =
        `<div class="case-meta"><strong>${escapeHtml(c.name)}</strong>` +
        `<span class="muted">${escapeHtml(c.created_at)}</span></div>` +
        `<div class="case-ops">` +
        `<button class="btn small" data-act="load">加载</button>` +
        `<button class="btn small danger" data-act="del">删除</button></div>`;
      li.querySelector('[data-act="load"]').addEventListener("click", () => loadCase(c.id));
      li.querySelector('[data-act="del"]').addEventListener("click", async () => {
        if (confirm(`删除案例「${c.name}」？`)) {
          await api(`/api/cases/${c.id}`, { method: "DELETE" });
          refreshCases();
        }
      });
      ul.appendChild(li);
    }
  } catch (err) {
    ul.innerHTML = `<li class="muted">加载失败：${escapeHtml(err.message)}</li>`;
  }
}

async function loadCase(id) {
  const c = await api(`/api/cases/${id}`);
  exprEl.value = c.expression;
  targetEl.value = c.target_unit || "";
  $("#caseName").value = c.name;
  varsEl.innerHTML = "";
  const vars = c.variables || {};
  const uncs = c.uncertainties || {};
  const names = Object.keys(vars);
  if (names.length === 0) addVarRow();
  else names.forEach((n) => addVarRow(n, vars[n], uncs[n] || ""));
  runCheck();
}

async function saveCase() {
  const name =
    $("#caseName").value.trim() || exprEl.value.trim().slice(0, 30) || "未命名案例";
  try {
    await api("/api/cases", {
      method: "POST",
      body: JSON.stringify({
        name,
        expression: exprEl.value,
        variables: collectVariables(),
        uncertainties: collectUncertainties(),
        target_unit: targetEl.value,
      }),
    });
    $("#caseName").value = name;
    refreshCases();
  } catch (err) {
    alert(`保存失败：${err.message}`);
  }
}

/* ---------------- 单位帮助 ---------------- */

async function loadUnits() {
  const box = $("#unitsHelp");
  try {
    const data = await api("/api/units");
    const groups = {};
    for (const u of data.units) (groups[u.category] ??= []).push(u);
    const parts = [];
    for (const [cat, list] of Object.entries(groups)) {
      const rows = list
        .map((u) => {
          const factor = u.factor !== 1 ? `<td class="muted mono">×${fmt(u.factor)}</td>` : "<td></td>";
          return `<tr><td class="mono">${escapeHtml(u.name)}</td><td>${escapeHtml(u.description)}</td>${factor}</tr>`;
        })
        .join("");
      parts.push(`<details><summary>${escapeHtml(cat)}（${list.length}）</summary><table>${rows}</table></details>`);
    }
    const constRows = data.constants
      .map((c) => `<tr><td class="mono">${escapeHtml(c.name)}</td><td>${escapeHtml(c.description)}</td></tr>`)
      .join("");
    parts.push(`<details><summary>内置常量（${data.constants.length}）</summary><table>${constRows}</table></details>`);
    const fnRows = data.functions
      .map((f) => `<tr><td class="mono">${escapeHtml(f.name)}(x)</td><td>${escapeHtml(f.description)}</td></tr>`)
      .join("");
    parts.push(`<details open><summary>函数（${data.functions.length}）</summary><table>${fnRows}</table></details>`);
    box.innerHTML = parts.join("");
  } catch (err) {
    box.innerHTML = `<p class="muted">加载失败：${escapeHtml(err.message)}</p>`;
  }
}

/* ---------------- 事件绑定 ---------------- */

$("#check").addEventListener("click", runCheck);
$("#addVar").addEventListener("click", () => addVarRow());
$("#saveCase").addEventListener("click", saveCase);
$("#exportAll").addEventListener("click", async () => {
  const res = await fetch("/api/export");
  download("dimcheck_cases.json", await res.text());
});
$("#exportResult").addEventListener("click", () => {
  if (lastExport) download("dimcheck_result.json", JSON.stringify(lastExport, null, 2));
});
$("#sample").addEventListener("click", () => {
  exprEl.value = "0.5*mass*v^2 + mass*g0*h0";
  targetEl.value = "kJ";
  varsEl.innerHTML = "";
  addVarRow("mass", "2 kg", "0.01 kg");
  addVarRow("v", "10 m/s", "0.2 m/s");
  addVarRow("h0", "5 m", "0.05 m");
  $("#caseName").value = "动能 + 重力势能";
  runCheck();
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runCheck();
});

addVarRow();
refreshCases();
loadUnits();
