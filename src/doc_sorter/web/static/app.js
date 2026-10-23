"use strict";

const $ = (id) => document.getElementById(id);
let current = "";  // browsed folder, relative to the root
let plan = null;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function show(id, text, className) {
  const node = $(id);
  node.hidden = !text;
  node.textContent = text || "";
  if (className !== undefined) node.className = className;
}

async function browse(path) {
  const data = await api("/api/folders?path=" + encodeURIComponent(path));
  current = data.path;
  $("current").textContent = current || "(root)";
  const box = $("folders");
  box.replaceChildren();
  if (data.parent !== null) {
    const up = el("button", "..");
    up.onclick = () => browse(data.parent);
    box.append(up);
  }
  for (const name of data.folders) {
    const button = el("button", name);
    button.onclick = () => browse(current ? current + "/" + name : name);
    box.append(button);
  }
  if (!data.folders.length && data.parent === null) box.append(el("span", "No subfolders.", "muted"));
}

async function preview() {
  show("error", "");
  $("result").hidden = true;
  $("preview").disabled = true;
  try {
    await api("/api/plan", {
      folder: current,
      instruction: $("instruction").value,
      dest: $("dest").value,
      recursive: $("recursive").checked,
      copy_files: $("copy").checked,
    });
    $("progress").hidden = false;
    await poll();
  } catch (error) {
    show("error", error.message);
  } finally {
    $("preview").disabled = false;
    $("progress").hidden = true;
  }
}

async function poll() {
  for (;;) {
    const job = await api("/api/job");
    const share = job.total ? job.done / job.total : 0;
    $("bar").style.width = Math.round(share * 100) + "%";
    $("progress-text").textContent = job.total ? `${job.done + 1} / ${job.total}  ${job.current}` : "Starting...";
    if (job.state === "error") throw new Error(job.error);
    if (job.state === "done") return render(job.plan);
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

function rulesText(rules) {
  const lines = rules.categories.map((c) => `${c.name}: ${c.folder}/${c.filename}`);
  if (rules.fallback) {
    const name = rules.open ? "New categories" : "Everything else";
    lines.push(`${name}: ${rules.fallback.folder}/${rules.fallback.filename}`);
  } else {
    lines.push("Everything else: stays in place");
  }
  return lines.join("\n");
}

function render(data) {
  plan = data;
  $("rules").replaceChildren(el("strong", "Rules"), el("pre", rulesText(data.rules)));
  $("rules").querySelector("pre").style.margin = "4px 0 0";

  const body = $("entries");
  body.replaceChildren();
  for (const entry of data.entries.filter((e) => e.status === "sort")) {
    const row = el("tr");
    row.dataset.index = entry.index;
    const box = el("input");
    box.type = "checkbox";
    box.checked = true;
    const target = el("input");
    target.type = "text";
    target.value = entry.target;
    target.dataset.original = entry.target;
    box.onchange = update;
    target.oninput = update;
    const check = el("td");
    check.append(box);
    const after = el("td");
    after.append(target);
    row.append(check, el("td", entry.source, "source"), after);
    body.append(row);
  }

  const others = $("others");
  others.replaceChildren();
  for (const [status, title] of [["keep", "Unchanged"], ["skip", "Skipped"], ["error", "Errors"]]) {
    const group = data.entries.filter((e) => e.status === status);
    if (!group.length) continue;
    const list = el("ul");
    for (const e of group) list.append(el("li", `${e.source} (${e.reason})`));
    others.append(el("p", `${title} (${group.length})`), list);
  }
  $("all").checked = true;
  $("result").hidden = false;
  update();
}

function rows() {
  return [...$("entries").querySelectorAll("tr")];
}

function tree(paths) {
  const root = {};
  for (const path of paths) {
    let node = root;
    const parts = path.split("/");
    parts.forEach((part, i) => {
      const key = i === parts.length - 1 ? part : part + "/";
      node[key] = node[key] || {};
      node = node[key];
    });
  }
  const lines = [];
  const walk = (node, prefix) => {
    const keys = Object.keys(node).sort((a, b) => a.localeCompare(b));
    keys.forEach((key, i) => {
      const last = i === keys.length - 1;
      lines.push(prefix + (last ? "└── " : "├── ") + key);
      walk(node[key], prefix + (last ? "    " : "│   "));
    });
  };
  walk(root, "");
  return lines.join("\n");
}

function update() {
  const chosen = rows().filter((row) => {
    const on = row.querySelector("input[type=checkbox]").checked;
    row.classList.toggle("off", !on);
    return on;
  });
  const paths = chosen.map((row) => row.querySelector("input[type=text]").value.trim()).filter(Boolean);
  const top = (plan.dest || plan.folder || "(root)") + "/";
  $("tree").textContent = paths.length ? top + "\n" + tree(paths) : "Nothing selected.";
  const verb = plan.copy ? "Copy" : "Move";
  $("apply").textContent = `${verb} ${chosen.length} file(s)`;
  $("apply").disabled = !chosen.length;
}

async function apply() {
  const selected = [];
  const targets = {};
  for (const row of rows()) {
    if (!row.querySelector("input[type=checkbox]").checked) continue;
    const index = Number(row.dataset.index);
    const input = row.querySelector("input[type=text]");
    selected.push(index);
    if (input.value.trim() !== input.dataset.original) targets[index] = input.value.trim();
  }
  $("apply").disabled = true;
  try {
    const data = await api("/api/apply", { selected, targets });
    $("result").hidden = true;
    show("message", `Done: ${data.applied} file(s). Log: ${data.log}`, "ok");
    await browse(data.dest);
  } catch (error) {
    show("error", error.message);
    $("apply").disabled = false;
  }
}

async function undo() {
  try {
    const data = await api("/api/undo", { folder: current });
    const kept = data.problems.length ? ` Kept: ${data.problems.join(" ")}` : "";
    show("message", `Restored ${data.restored} file(s), removed ${data.removed_dirs} empty folder(s).${kept}`,
      data.problems.length ? "error" : "ok");
    await browse(current);
  } catch (error) {
    show("message", error.message, "error");
  }
}

async function start() {
  const info = await api("/api/info");
  $("root").textContent = info.root;
  $("model").textContent = info.vision_model === info.model
    ? `model: ${info.model}` : `model: ${info.model}, vision: ${info.vision_model}`;
  $("preview").onclick = preview;
  $("apply").onclick = apply;
  $("undo").onclick = undo;
  $("all").onchange = () => {
    for (const row of rows()) row.querySelector("input[type=checkbox]").checked = $("all").checked;
    update();
  };
  await browse("");
  // after a reload, show a plan that is still waiting or being made
  const job = await api("/api/job");
  if (job.state !== "idle") $("instruction").value = job.instruction;
  if (job.state === "done") {
    await browse(job.plan.folder);
    render(job.plan);
  }
  if (job.state === "running") {
    $("preview").disabled = true;
    $("progress").hidden = false;
    try {
      await poll();
    } catch (error) {
      show("error", error.message);
    } finally {
      $("preview").disabled = false;
      $("progress").hidden = true;
    }
  }
}

start().catch((error) => show("error", error.message));
