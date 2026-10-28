/* Asset presentation is independent of workflow submission and model calls. */
const assetPages = new Map();
function pageRows(key, rows, render, signature = "") {
  const settings = assetPages.get(key) || { page: 1, size: 8, signature };
  if (settings.signature !== signature) settings.page = 1;
  settings.signature = signature;
  const pages = Math.max(1, Math.ceil(rows.length / settings.size));
  settings.page = Math.min(settings.page, pages);
  assetPages.set(key, settings);
  const host = document.getElementById(key);
  const anchor = host.closest(".scroll") || host;
  let pager = document.getElementById(key + "Pager");
  if (!pager) {
    pager = document.createElement("div");
    pager.id = key + "Pager";
    pager.className = "list-pager";
    anchor.after(pager);
  }
  pager.innerHTML = `<span>共 ${rows.length} 项 · ${settings.page} / ${pages} 页</span><div><label>每页 <select aria-label="每页显示数量">${[8, 16, 32].map((n) => `<option ${n === settings.size ? "selected" : ""}>${n}</option>`).join("")}</select></label><button class="btn secondary" data-prev ${settings.page === 1 ? "disabled" : ""} aria-label="上一页">上一页</button><button class="btn secondary" data-next ${settings.page === pages ? "disabled" : ""} aria-label="下一页">下一页</button></div>`;
  pager.querySelector("select").onchange = (e) => {
    settings.size = Number(e.target.value);
    settings.page = 1;
    render();
  };
  pager.querySelector("[data-prev]").onclick = () => {
    settings.page--;
    render();
  };
  pager.querySelector("[data-next]").onclick = () => {
    settings.page++;
    render();
  };
  host
    .querySelectorAll("td b, td .mono")
    .forEach((el) => (el.title = el.textContent));
  return rows.slice(
    (settings.page - 1) * settings.size,
    settings.page * settings.size,
  );
}
function assetFilter(key, render) {
  const host = document.getElementById(key);
  let input = document.getElementById(key + "Search");
  if (!input) {
    const wrapper = document.createElement("div");
    wrapper.className = "asset-filter";
    wrapper.innerHTML = `<input type="search" id="${key}Search" aria-label="搜索资产名称、类型和路径" placeholder="搜索名称、类型或路径…" />`;
    host.closest(".scroll").before(wrapper);
    input = wrapper.firstElementChild;
    input.oninput = render;
  }
  return input.value.trim().toLowerCase();
}
function filterAssetRows(rows, query) {
  return rows.filter(
    (r) =>
      !query ||
      `${r.name} ${r.path} ${r.kind} ${r.run || ""}`
        .toLowerCase()
        .includes(query),
  );
}
function emptyPreview(
  message = "选择一份资产开始查看",
  detail = "数据、图表与报告会在这里呈现",
) {
  return `<div class="asset-empty"><span aria-hidden="true">▤</span><strong>${esc(message)}</strong><p>${esc(detail)}</p></div>`;
}
function jsonNode(value, key = "根节点", depth = 0) {
  const node = document.createElement("div");
  node.className = "json-node";
  if (value === null || typeof value !== "object") {
    node.innerHTML = `<span class="json-key">${esc(key)}</span><span class="json-value ${typeof value}">${esc(JSON.stringify(value))}</span>`;
    return node;
  }
  const entries = Object.entries(value);
  const details = document.createElement("details");
  details.open = depth === 0;
  details.innerHTML = `<summary><span class="json-key">${esc(key)}</span><span class="muted">${Array.isArray(value) ? "数组" : "对象"} · ${entries.length} 项</span></summary><div class="json-children"></div>`;
  node.append(details);
  let offset = 0;
  const append = () => {
    const children = details.lastElementChild;
    children.querySelector(":scope > button")?.remove();
    entries
      .slice(offset, offset + 40)
      .forEach(([k, v]) => children.append(jsonNode(v, k, depth + 1)));
    offset += 40;
    if (offset < entries.length) {
      const more = document.createElement("button");
      more.className = "btn secondary";
      more.textContent = `再显示 40 项（剩余 ${entries.length - offset}）`;
      more.onclick = append;
      children.append(more);
    }
  };
  if (details.open) append();
  details.ontoggle = () => {
    if (details.open && !offset) append();
  };
  return node;
}
function readableText(text) {
  // Escape before formatting. Uploaded Markdown never becomes arbitrary HTML.
  let inCode = false;
  return text
    .split("\n")
    .map((line) => {
      if (/^```/.test(line)) {
        inCode = !inCode;
        return `<div class="reading-rule">${inCode ? "代码片段" : "代码结束"}</div>`;
      }
      if (inCode) return `<div class="reading-code">${esc(line) || " "}</div>`;
      const heading = /^(#{1,4})\s+(.+)/.exec(line);
      if (heading)
        return `<h${heading[1].length + 1}>${esc(heading[2])}</h${heading[1].length + 1}>`;
      if (/^\s*[-*]\s+/.test(line))
        return `<p class="reading-bullet">• ${esc(line.replace(/^\s*[-*]\s+/, ""))}</p>`;
      return line.trim()
        ? `<p>${esc(line)
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
            .replace(/`([^`]+)`/g, "<code>$1</code>")}</p>`
        : "<div class='reading-space'></div>";
    })
    .join("");
}
function codeLines(content, start = 1, python = false) {
  return `<div class="code-lines">${content
    .split("\n")
    .map((line, i) => {
      let html = esc(line);
      if (python)
        html = html.replace(
          /(#[^\n]*$)|\b(def|class|return|import|from|if|else|elif|for|while|in|and|or|not|True|False|None|try|except|with|as|raise|self)\b/g,
          (match, comment) =>
            `<span class="${comment ? "code-comment" : "code-keyword"}">${match}</span>`,
        );
      return `<div><span class="line-number">${start + i}</span><code>${html || " "}</code></div>`;
    })
    .join("")}</div>`;
}
function drawCsvChart(host, data) {
  const numeric = data.headers
    .map((h, i) => ({ h, i }))
    .filter(
      ({ i }) =>
        data.rows.some((row) => row[i]?.trim()) &&
        data.rows.every(
          (row) => !row[i]?.trim() || Number.isFinite(Number(row[i])),
        ),
    );
  if (!numeric.length || !data.rows.length) {
    host.innerHTML = `<div class="mini-note">当前页没有可绘制的数值列，完整内容见下方表格。</div>`;
    return;
  }
  host.innerHTML = `<div class="chart-controls"><strong>数据趋势</strong><label>横轴 <select aria-label="图表横轴">${data.headers.map((h, i) => `<option value="${i}">${esc(h)}</option>`).join("")}</select></label><label>数值 <select aria-label="图表数值列">${numeric.map(({ h, i }) => `<option value="${i}">${esc(h)}</option>`).join("")}</select></label></div><div class="csv-chart"></div><p class="muted chart-caption">展示当前页的有效数据点，切换分页可查看其余记录。</p>`;
  const controls = host.querySelectorAll("select");
  controls[1].value = String((numeric.find(({ i }) => i > 0) || numeric[0]).i);
  const draw = () => {
    const x = Number(controls[0].value),
      y = Number(controls[1].value);
    const points = data.rows
      .filter((r) => r[y]?.trim())
      .map((r, i) => ({ label: r[x] || String(i + 1), value: Number(r[y]) }));
    const min = Math.min(0, ...points.map((p) => p.value)),
      max = Math.max(0, ...points.map((p) => p.value));
    const range = max - min || 1;
    const py = (v) => 184 - ((v - min) / range) * 154;
    const px = (i) => 60 + (i / Math.max(points.length - 1, 1)) * 490;
    const fmt = (n) => Number(n.toPrecision(4)).toLocaleString();
    host.querySelector(".csv-chart").innerHTML =
      `<svg viewBox="0 0 600 236" role="img" aria-label="${esc(data.headers[y])} 当前页趋势图">${[min, min + range / 2, max].map((v) => `<line x1="60" x2="555" y1="${py(v)}" y2="${py(v)}" stroke="#e5ecef"/><text x="50" y="${py(v) + 4}" text-anchor="end">${esc(fmt(v))}</text>`).join("")}<polyline points="${points.map((p, i) => `${px(i)},${py(p.value)}`).join(" ")}" fill="none" stroke="#128b82" stroke-width="2.5"/>${points.map((p, i) => `<circle cx="${px(i)}" cy="${py(p.value)}" r="4" fill="#128b82"><title>${esc(p.label)}：${esc(p.value)}</title></circle>`).join("")}${points
        .filter((_, i) => i === 0 || i === points.length - 1)
        .map(
          (p) =>
            `<text x="${px(points.indexOf(p))}" y="211" text-anchor="middle">${esc(p.label.length > 18 ? p.label.slice(0, 16) + "…" : p.label)}</text>`,
        )
        .join("")}</svg>`;
  };
  controls.forEach((s) => (s.onchange = draw));
  draw();
}
async function renderAssetPreview(path, container, page = 1) {
  const p = rel(path),
    host = typeof container === "string" ? $(container) : container;
  const token = Symbol();
  host.previewToken = token;
  host.innerHTML = emptyPreview("正在读取资产…", p);
  try {
    const d = await api(
      `/api/selfopt/preview?path=${encodeURIComponent(p)}&page=${page}`,
    );
    if (host.previewToken !== token) return;
    const raw = `/api/selfopt/raw?path=${encodeURIComponent(p)}`;
    const download = `/api/selfopt/download?path=${encodeURIComponent(p)}`;
    host.innerHTML = `<div class="asset-meta"><span class="asset-type">${esc((d.format || d.kind || "file").toUpperCase())}</span><div><strong>${esc(d.name)}</strong><span>${bytes(d.size)}</span></div><a class="btn secondary" href="${download}" download>下载文件 ↓</a></div><div class="preview-tabs" role="group" aria-label="预览方式"></div><div class="asset-content"></div><div class="preview-pagination"></div>`;
    const body = host.querySelector(".asset-content"),
      tabs = host.querySelector(".preview-tabs");
    const mode = (label, draw) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.onclick = () => {
        tabs.querySelectorAll("button").forEach((b) => {
          b.classList.remove("active");
          b.setAttribute("aria-pressed", "false");
        });
        button.classList.add("active");
        button.setAttribute("aria-pressed", "true");
        body.replaceChildren();
        draw();
      };
      tabs.append(button);
      if (tabs.children.length === 1) button.click();
    };
    if (["png", "jpg", "jpeg", "webp", "svg"].includes(d.kind)) {
      mode("图片预览", () => {
        body.innerHTML = `<div class="image-controls"><button class="btn secondary" type="button">查看原始尺寸</button><span class="muted image-size">加载中…</span></div><div class="asset-image-stage"><img src="${raw}" alt="${esc(d.name)}" /></div>`;
        const img = body.querySelector("img");
        img.onload = () => {
          body.querySelector(".image-size").textContent =
            `${img.naturalWidth} × ${img.naturalHeight} px`;
        };
        img.onerror = () => {
          body.querySelector(".image-size").textContent =
            "图片无法解码，可下载查看";
        };
        body.querySelector("button").onclick = (e) => {
          img.classList.toggle("original");
          e.target.textContent = img.classList.contains("original")
            ? "适应容器"
            : "查看原始尺寸";
        };
      });
    } else if (["pdf", "html"].includes(d.kind)) {
      mode(d.kind === "pdf" ? "文档预览" : "报告预览", () => {
        body.innerHTML = `<iframe class="preview-frame" title="${esc(d.name)}" ${d.kind === "html" ? 'sandbox=""' : ""} src="${raw}"></iframe>`;
      });
    } else if (d.kind === "csv") {
      mode("数据与图表", () => {
        body.innerHTML = `<div class="csv-summary"><span><strong>${d.total}</strong> 条记录</span><span><strong>${d.headers.length}</strong> 个字段</span><span>第 ${page} 页</span></div><div class="csv-chart-host"></div><p class="muted chart-caption">左右滚动表格可查看全部字段；单元格最多预览 4,000 字符。</p><div class="data-table-scroll"><table class="data-table"><thead><tr>${d.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${d.rows.map((row) => `<tr>${d.headers.map((_, i) => `<td>${esc(row[i] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>${d.columns_limited ? '<p class="mini-note">宽表最多预览 60 列，完整字段请下载文件。</p>' : ""}`;
        drawCsvChart(body.querySelector(".csv-chart-host"), d);
      });
      mode("当前页文本", () => {
        body.innerHTML = codeLines(
          [d.headers, ...d.rows]
            .map((row) => row.map((c) => JSON.stringify(c)).join(","))
            .join("\n"),
        );
      });
    } else if (Object.hasOwn(d, "data")) {
      mode("结构视图", () => body.append(jsonNode(d.data)));
      mode("JSON 源文", () => {
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(d.data, null, 2);
        body.append(pre);
      });
    } else if (d.kind === "text") {
      if (["md", "txt"].includes(d.format))
        mode("阅读视图", () => {
          body.innerHTML = `<article class="reading-view">${readableText(d.content)}</article>`;
        });
      mode(d.format === "py" ? "协议代码" : "带行号文本", () => {
        body.innerHTML = codeLines(
          d.content,
          (page - 1) * d.page_size + 1,
          d.format === "py",
        );
      });
      if (d.truncated)
        body.insertAdjacentHTML(
          "beforeend",
          '<p class="mini-note">本页超长文本已截断，请下载完整文件。</p>',
        );
    } else
      body.innerHTML = emptyPreview(
        "此文件可下载查看",
        d.hint || "暂不支持在线解析此格式",
      );
    if (d.page_size && d.total > d.page_size) {
      const pages = Math.ceil(d.total / d.page_size);
      const footer = host.querySelector(".preview-pagination");
      footer.innerHTML = `<span>共 ${d.total} ${d.kind === "csv" ? "条记录" : "行"} · ${page} / ${pages} 页</span><div><button class="btn secondary" ${page <= 1 ? "disabled" : ""}>上一页</button><button class="btn secondary" ${page >= pages ? "disabled" : ""}>下一页</button></div>`;
      const buttons = footer.querySelectorAll("button");
      buttons[0].onclick = () => renderAssetPreview(p, host, page - 1);
      buttons[1].onclick = () => renderAssetPreview(p, host, page + 1);
    }
    return true;
  } catch (error) {
    if (host.previewToken === token)
      host.innerHTML = emptyPreview("暂时无法预览", error.message);
    return false;
  }
}
function installAssetUploads() {
  for (const [view, defaultDestination] of [
    ["assets", "protocol"],
    ["outputs", "output"],
    ["experiments", "experiment"],
  ]) {
    const section = $("view-" + view),
      box = document.createElement("details");
    box.className = "upload-panel panel";
    box.innerHTML = `<summary><span>＋ 导入本地文件</span><span class="muted">上传数据、协议与报告</span></summary><div class="upload-body"><div class="upload-options">${view === "assets" ? `<label>导入类型<select class="upload-destination"><option value="protocol">协议代码 · .py</option><option value="prompt">Prompt · .txt</option><option value="scene_output">场景输出 · 数据 / 图片 / 报告</option></select></label><label class="upload-generation-label">目标世代<input class="upload-generation" value="generation_0" aria-label="上传的目标世代" /></label>` : `<span class="mini-note">保存至 ${view === "outputs" ? "course_project/outputs/uploads" : "Experiments/uploads"}，导入后可直接在本页预览。</span>`}</div><div class="upload-drop"><strong>拖放文件到这里，或选择文件</strong><span>单个文件最大 10 MB · 每批最多 10 个 · 同名文件需先重命名</span><label class="btn secondary">选择文件<input class="upload-files" type="file" multiple aria-label="选择要导入的文件" /></label><span class="upload-selection">尚未选择文件</span></div><div class="actions"><button type="button" class="btn upload-submit" disabled>开始上传</button><span class="muted upload-help">${view === "assets" ? "导入到顶部所选场景；协议仅保存，需手动选择后验证。" : "支持 CSV、JSON、Markdown、文本、HTML、PDF、PNG、JPG、WebP 和 SVG。"}</span></div><ul class="upload-results" aria-live="polite"></ul></div>`;
    section.prepend(box);
    const input = box.querySelector(".upload-files"),
      drop = box.querySelector(".upload-drop"),
      submit = box.querySelector(".upload-submit"),
      destination = box.querySelector(".upload-destination");
    let files = [],
      busy = false;
    const accept = () => {
      const dest = destination?.value || defaultDestination;
      input.accept =
        dest === "protocol"
          ? ".py"
          : dest === "prompt"
            ? ".txt"
            : ".csv,.json,.md,.txt,.log,.html,.pdf,.png,.jpg,.jpeg,.webp,.svg";
      box
        .querySelector(".upload-generation-label")
        ?.toggleAttribute("hidden", dest !== "protocol");
    };
    if (destination) destination.onchange = accept;
    accept();
    const select = (selected) => {
      if (busy) return;
      files = Array.from(selected).slice(0, 10);
      box.querySelector(".upload-selection").textContent = files.length
        ? files.map((f) => f.name).join("、")
        : "尚未选择文件";
      submit.disabled = !files.length;
    };
    input.onchange = () => select(input.files);
    drop.ondragover = (e) => {
      e.preventDefault();
      drop.classList.add("dragging");
    };
    drop.ondragleave = () => drop.classList.remove("dragging");
    drop.ondrop = (e) => {
      e.preventDefault();
      drop.classList.remove("dragging");
      select(e.dataTransfer.files);
    };
    submit.onclick = async () => {
      busy = true;
      submit.disabled = true;
      input.disabled = true;
      if (destination) destination.disabled = true;
      const result = box.querySelector(".upload-results");
      result.replaceChildren();
      let first = null,
        successes = 0;
      const uploadScene = state.selectedScene;
      const batch = [...files],
        uploadDestination = destination?.value || defaultDestination;
      const generation =
        box.querySelector(".upload-generation")?.value || "generation_0";
      for (const file of batch) {
        const item = document.createElement("li");
        item.textContent = `${file.name} · 上传中…`;
        result.append(item);
        try {
          if (!file.size || file.size > 10 * 1024 * 1024)
            throw new Error("请选择非空且不超过 10 MB 的文件");
          const params = new URLSearchParams({
            name: file.name,
            destination: uploadDestination,
            scene: uploadScene,
            generation,
          });
          const response = await fetch(`/api/selfopt/upload?${params}`, {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: file,
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error);
          first ||= data.path;
          successes++;
          item.className = "upload-success";
          item.textContent = `${file.name} · 已导入 ${data.path}`;
        } catch (error) {
          item.className = "upload-error";
          item.textContent = `${file.name} · ${error.message}`;
        }
      }
      if (successes) {
        await loadState().catch((error) => setStatus(error.message, "error"));
        if (view === "assets") {
          $("filePreviewTitle").textContent = "导入文件预览";
          $("filePreviewModal").classList.add("show");
          renderAssetPreview(first, "filePreviewBody");
        } else if (view === "outputs") previewArtifact(first);
        else previewExperimentArtifact(first);
      }
      setStatus(
        `导入完成：${successes} / ${batch.length} 个文件`,
        successes === batch.length ? "ready" : "error",
      );
      input.disabled = false;
      if (destination) destination.disabled = false;
      files = [];
      input.value = "";
      box.querySelector(".upload-selection").textContent = "尚未选择文件";
      busy = false;
    };
  }
  for (const id of [
    "outputPreview",
    "experimentPreview",
    "aiPreview",
    "iterationPreview",
  ])
    $(id).innerHTML = emptyPreview();
  installFileReader();
}

function installFileReader() {
  const editor = $("fileEditor"),
    controls = document.createElement("div"),
    reader = document.createElement("div");
  controls.className = "preview-tabs editor-modes";
  controls.innerHTML =
    '<button type="button" class="active" aria-pressed="true">阅读预览</button><button type="button" aria-pressed="false">编辑源文件</button>';
  reader.id = "codeReader";
  reader.innerHTML = emptyPreview(
    "选择左侧文件",
    "代码支持高亮和行号，长文本可以翻页阅读",
  );
  editor.before(controls, reader);
  editor.hidden = true;
  const buttons = controls.querySelectorAll("button");
  buttons.forEach(
    (button, i) =>
      (button.onclick = () => {
        editor.hidden = i === 0;
        reader.hidden = i === 1;
        buttons.forEach((b, index) => {
          b.classList.toggle("active", index === i);
          b.setAttribute("aria-pressed", String(index === i));
        });
        if (i === 0) refreshFileReader();
        else if (state.currentFile) editor.focus();
      }),
  );
}
function refreshFileReader() {
  const reader = $("codeReader");
  if (!reader || reader.hidden || !state.currentFile) return;
  if (state.editorDirty) {
    reader.previewToken = Symbol();
    const lines = $("fileEditor").value.split("\n");
    reader.innerHTML =
      '<p class="mini-note">正在预览尚未保存的编辑内容（最多 500 行），保存后可翻页阅读完整文件。</p>' +
      codeLines(
        lines.slice(0, 500).join("\n").slice(0, 100000),
        1,
        state.currentFile.endsWith(".py"),
      );
  } else renderAssetPreview(state.currentFile, reader);
}
