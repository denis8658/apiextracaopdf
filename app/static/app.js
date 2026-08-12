const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  file: null,
  documentId: null,
  jobId: null,
  result: null,
  format: "text",
  pollController: null,
};

const elements = {
  form: $("#uploadForm"), fileInput: $("#pdfFile"), dropzone: $("#dropzone"),
  dropTitle: $("#dropTitle"), dropHint: $("#dropHint"), apiBase: $("#apiBase"),
  engine: $("#engine"), retain: $("#retainOriginal"), submit: $("#submitButton"),
  structureOutput: $("#structureOutput"),
  pages: $("#pages"), clienteId: $("#clienteId"), obraId: $("#obraId"),
  status: $("#serviceStatus"), empty: $("#emptyState"), progress: $("#progressCard"),
  caption: $("#processCaption"), documentName: $("#documentName"), documentMeta: $("#documentMeta"),
  statusPill: $("#statusPill"), progressMessage: $("#progressMessage"),
  progressPercent: $("#progressPercent"), progressBar: $("#progressBar"),
  documentId: $("#documentId"), jobId: $("#jobId"), engineUsed: $("#engineUsed"),
  results: $("#resultsSection"), resultSummary: $("#resultSummary"),
  resultContent: $("#resultContent"), copy: $("#copyButton"), download: $("#downloadButton"),
  error: $("#errorToast"), errorTitle: $("#errorTitle"), errorMessage: $("#errorMessage"),
};

const baseUrl = () => elements.apiBase.value.replace(/\/$/, "");
const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
});

function showError(title, error) {
  const payload = error?.error || error;
  elements.errorTitle.textContent = title;
  elements.errorMessage.textContent = payload?.message
    ? `${payload.message}${payload.request_id ? ` · ID ${payload.request_id}` : ""}`
    : String(error?.message || error || "Erro desconhecido");
  elements.error.classList.remove("hidden");
}

async function request(path, options = {}) {
  const response = await fetch(`${baseUrl()}${path}`, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw payload;
  return payload;
}

async function checkHealth() {
  elements.status.className = "service-status";
  elements.status.lastElementChild.textContent = "verificando serviço";
  try {
    await request("/health");
    elements.status.classList.add("online");
    elements.status.lastElementChild.textContent = "serviço disponível";
  } catch {
    elements.status.classList.add("offline");
    elements.status.lastElementChild.textContent = "serviço indisponível";
  }
}

function setFile(file) {
  if (!file) return;
  if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    showError("Arquivo inválido", { message: "Selecione um documento PDF." });
    return;
  }
  state.file = file;
  elements.dropzone.classList.add("has-file");
  elements.dropTitle.textContent = file.name;
  elements.dropHint.textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB · clique para trocar`;
}

function setProgress(document) {
  const percent = document.progress?.percent ?? 0;
  const labels = { queued: "Na fila", processing: "Extraindo conteúdo", completed: "Concluído", failed: "Falhou", cancelled: "Cancelado" };
  elements.statusPill.textContent = document.status;
  elements.statusPill.classList.toggle("failed", ["failed", "cancelled"].includes(document.status));
  elements.progressMessage.textContent = labels[document.status] || document.status;
  elements.progressPercent.textContent = `${percent}%`;
  elements.progressBar.style.width = `${percent}%`;
  elements.engineUsed.textContent = document.engine_used || "seleção pendente";
  const page = document.progress?.current_page;
  const total = document.progress?.total_pages;
  elements.documentMeta.textContent = total ? `${page || 0} de ${total} páginas` : "Lendo metadados";
  elements.caption.textContent = labels[document.status] || "Processando";
}

async function pollDocument(documentId, signal) {
  for (;;) {
    const document = await request(`/api/v1/documents/${documentId}`, { signal });
    setProgress(document);
    if (document.status === "completed") return document;
    if (["failed", "cancelled"].includes(document.status)) {
      if (state.jobId) {
        try {
          const job = await request(`/api/v1/extraction-jobs/${state.jobId}`, { signal });
          if (job.error_message_safe) {
            throw { message: `${job.error_message_safe} (${job.error_code || document.status})` };
          }
        } catch (error) {
          if (error?.message) throw error;
        }
      }
      throw { message: `O processamento terminou com status “${document.status}”.` };
    }
    await sleep(2000, signal);
  }
}

function contentFor(format) {
  if (!state.result) return "";
  const document = state.result.document || state.result;
  const pages = document.pages || [];
  if (format === "text") {
    return document.plain_text || pages.map((page) => `Página ${page.page_number}\n${page.plain_text || ""}`).join("\n\n");
  }
  if (format === "markdown") {
    return document.markdown || pages.map((page) => `# Página ${page.page_number}\n\n${page.markdown || ""}`).join("\n\n---\n\n");
  }
  return JSON.stringify(state.result, null, 2);
}

function renderResult(format = state.format) {
  state.format = format;
  $$(".tab").forEach((tab) => {
    const active = tab.dataset.format === format;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  const document = state.result?.document || state.result || {};
  const pages = document.pages || [];
  const items = state.result?.itens || [];
  const chars = contentFor("text").length;
  const methods = [...new Set(pages.map((page) => page.extraction_method).filter(Boolean))];
  const engine = document.engine || methods.join(" + ") || "—";
  elements.resultSummary.innerHTML = items.length
    ? `<span>${items.length} itens estruturados</span>`
    : `<span>${pages.length} páginas</span><span>${chars.toLocaleString("pt-BR")} caracteres</span><span>motor ${engine}</span>`;
  elements.resultContent.textContent = contentFor(format);
}

async function handleSubmit(event) {
  event.preventDefault();
  elements.error.classList.add("hidden");
  if (!state.file) { showError("Selecione um PDF", { message: "Escolha um arquivo antes de iniciar." }); return; }
  state.pollController?.abort();
  state.pollController = new AbortController();
  elements.submit.disabled = true;
  elements.submit.firstElementChild.textContent = "Enviando…";
  elements.results.classList.add("hidden");
  elements.empty.classList.add("hidden");
  elements.progress.classList.remove("hidden");
  elements.documentName.textContent = state.file.name;
  elements.documentMeta.textContent = "Enviando arquivo";
  elements.statusPill.textContent = "upload";
  elements.progressBar.style.width = "3%";

  const formData = new FormData();
  formData.append("file", state.file);
  formData.append("cliente_id", elements.clienteId.value);
  formData.append("obra_id", elements.obraId.value);
  formData.append("engine", elements.engine.value);
  formData.append("output_formats", "text,markdown,json");
  formData.append("retain_original", String(elements.retain.checked));
  formData.append("pages", elements.pages.value || "all");
  formData.append("structure_output", String(elements.structureOutput.checked));

  try {
    const upload = await request("/api/v1/documents", {
      method: "POST",
      headers: { "X-Request-ID": crypto.randomUUID(), "Idempotency-Key": crypto.randomUUID() },
      body: formData,
      signal: state.pollController.signal,
    });
    state.documentId = upload.document_id;
    state.jobId = upload.job_id;
    elements.documentId.textContent = state.documentId;
    elements.jobId.textContent = state.jobId;
    localStorage.setItem("leitor:lastDocument", state.documentId);
    await pollDocument(state.documentId, state.pollController.signal);
    state.result = await request(`/api/v1/documents/${state.documentId}/result?format=json`, { signal: state.pollController.signal });
    renderResult(state.result?.itens ? "json" : "text");
    elements.results.classList.remove("hidden");
    elements.results.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (error?.name !== "AbortError") showError("Não foi possível processar", error);
  } finally {
    elements.submit.disabled = false;
    elements.submit.firstElementChild.textContent = "Processar documento";
  }
}

elements.apiBase.value = localStorage.getItem("leitor:apiBase") || window.location.origin;
elements.apiBase.addEventListener("change", () => { localStorage.setItem("leitor:apiBase", baseUrl()); checkHealth(); });
elements.fileInput.addEventListener("change", () => setFile(elements.fileInput.files[0]));
elements.dropzone.addEventListener("dragover", (event) => { event.preventDefault(); elements.dropzone.classList.add("dragging"); });
elements.dropzone.addEventListener("dragleave", () => elements.dropzone.classList.remove("dragging"));
elements.dropzone.addEventListener("drop", (event) => { event.preventDefault(); elements.dropzone.classList.remove("dragging"); setFile(event.dataTransfer.files[0]); });
elements.form.addEventListener("submit", handleSubmit);
$("#dismissError").addEventListener("click", () => elements.error.classList.add("hidden"));
$$('.tab').forEach((tab) => tab.addEventListener("click", () => renderResult(tab.dataset.format)));
elements.copy.addEventListener("click", async () => { await navigator.clipboard.writeText(contentFor(state.format)); elements.copy.textContent = "Copiado"; setTimeout(() => { elements.copy.textContent = "Copiar"; }, 1400); });
elements.download.addEventListener("click", () => {
  const extensions = { text: "txt", markdown: "md", json: "json" };
  const blob = new Blob([contentFor(state.format)], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `extracao-${state.documentId || "documento"}.${extensions[state.format]}`;
  link.click();
  URL.revokeObjectURL(link.href);
});

checkHealth();
