/* eslint-disable no-console */
"use strict";

const vscode = require("vscode");

const CONTEXT_ROOT = ".automation-context/copilot";
const JOB_FILE = "bridge-job.json";
const JOB_STATE_FILE = "bridge-job-state.json";
const STATUS_FILE = "bridge-status.json";
const RESULT_FILE = "agent-result.json";
const MAX_FILE_BYTES = 80_000;
const MAX_SEARCH_FILES = 500;
const IGNORED_DIRECTORIES = new Set([".git", "node_modules", ".automation-context", ".venv", "venv", "site"]);

let output;
let processing = new Set();
let workspaceRoot;
let cancellation;
let copilotAccess = { status: "unknown", models: [] };
// A bridge job can be visible to more than one VS Code window. This id lets the
// first extension host claim execution before a newly opened window discovers it.
const bridgeInstanceId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function now() {
  return new Date().toISOString();
}

function normalizePath(value) {
  return String(value || "").replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+/g, "/");
}

function relativeWorkspacePath(value) {
  const normalized = normalizePath(value);
  if (!normalized || normalized === "." || normalized.includes("../") || normalized.startsWith("..")) {
    throw new Error(`Invalid workspace-relative path: ${value}`);
  }
  return normalized;
}

function packageUri(packagePath, fileName) {
  return vscode.Uri.joinPath(packagePath, fileName);
}

function parentUri(uri) {
  const separator = uri.path.lastIndexOf("/");
  return uri.with({ path: separator > 0 ? uri.path.slice(0, separator) : "/" });
}

function workspaceUri(relativePath) {
  return vscode.Uri.joinPath(workspaceRoot, relativeWorkspacePath(relativePath));
}

async function pathExists(uri) {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

async function readText(uri, limit = MAX_FILE_BYTES) {
  const bytes = await vscode.workspace.fs.readFile(uri);
  const safeBytes = bytes.byteLength > limit ? bytes.slice(0, limit) : bytes;
  const text = Buffer.from(safeBytes).toString("utf8");
  return bytes.byteLength > limit ? `${text}\n\n[Truncated at ${limit} bytes.]` : text;
}

async function writeJson(uri, value) {
  const temporary = vscode.Uri.joinPath(parentUri(uri), `.${uri.path.split("/").pop()}.tmp`);
  const content = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  await vscode.workspace.fs.writeFile(temporary, content);
  await vscode.workspace.fs.rename(temporary, uri, { overwrite: true });
}

async function readJson(uri) {
  try {
    return JSON.parse(await readText(uri, MAX_FILE_BYTES));
  } catch {
    return {};
  }
}

function log(message) {
  output.appendLine(`[${now()}] ${message}`);
}

async function writeBridgeStatus(status, details = {}) {
  if (!workspaceRoot) {
    return;
  }
  const root = vscode.Uri.joinPath(workspaceRoot, CONTEXT_ROOT);
  await vscode.workspace.fs.createDirectory(root);
  await writeJson(packageUri(root, STATUS_FILE), {
    status,
    updated_at: now(),
    extension: "criticalmanufacturing.cmf-content-ai-pipeline-bridge",
    workspace: workspaceRoot.fsPath,
    ...details,
  });
}

function cleanModelToken(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function describeModel(model) {
  return {
    id: String(model.id || ""),
    name: String(model.name || ""),
    vendor: String(model.vendor || ""),
    family: String(model.family || ""),
    version: String(model.version || ""),
    maxInputTokens: Number(model.maxInputTokens || 0),
  };
}

function modelMatches(model, requestedName) {
  const requested = cleanModelToken(requestedName);
  if (!requested) {
    return true;
  }
  const candidate = cleanModelToken([
    model.id,
    model.name,
    model.vendor,
    model.family,
    model.version,
  ].filter(Boolean).join(" "));
  return Boolean(candidate) && (candidate.includes(requested) || requested.includes(candidate));
}

async function selectConfiguredModel(requestedName) {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot" });
  const selected = models.find((model) => modelMatches(model, requestedName));
  if (!selected) {
    const available = models.map(describeModel);
    throw new Error(
      `No VS Code Copilot language model matched '${requestedName}'. Available models: ${JSON.stringify(available)}`,
    );
  }
  return selected;
}

async function checkCopilotAccess() {
  try {
    copilotAccess = { status: "awaiting_copilot_access", models: [] };
    await writeBridgeStatus("awaiting_copilot_access", {
      detail: "Waiting for VS Code Copilot language-model authorization when required.",
    });
    const models = await vscode.lm.selectChatModels({ vendor: "copilot" });
    copilotAccess = { status: "ready", models: models.map(describeModel) };
    await writeBridgeStatus("ready", {
      copilot_access: "ready",
      available_models: copilotAccess.models,
    });
    return copilotAccess;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    copilotAccess = { status: "consent_required", models: [], error: message };
    await writeBridgeStatus("consent_required", {
      copilot_access: "consent_required",
      error: message,
      detail: "Run the Content AI: Enable Copilot Bridge command once in VS Code to grant the platform-required Copilot consent.",
    });
    return copilotAccess;
  }
}

async function enableCopilotBridge() {
  const access = await checkCopilotAccess();
  if (access.status !== "ready") {
    vscode.window.showErrorMessage("Content AI could not access the configured Copilot language model. Check the Content AI Pipeline Bridge output channel.");
    output.show(true);
    return;
  }
  vscode.window.showInformationMessage("Content AI Copilot bridge is ready. Pending pipeline jobs will now run automatically.");
  await runPendingJobs();
}

function extractJson(text) {
  const trimmed = String(text || "").trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/i, "");
  try {
    return JSON.parse(trimmed);
  } catch {
    // Some models add a second JSON object or a short explanation after a valid
    // action. Find the first complete object instead of parsing from first `{`
    // through the final `}`, which would combine both responses.
    for (let start = trimmed.indexOf("{"); start >= 0; start = trimmed.indexOf("{", start + 1)) {
      let depth = 0;
      let quoted = false;
      let escaped = false;
      for (let index = start; index < trimmed.length; index += 1) {
        const character = trimmed[index];
        if (quoted) {
          if (escaped) {
            escaped = false;
          } else if (character === "\\") {
            escaped = true;
          } else if (character === '"') {
            quoted = false;
          }
          continue;
        }
        if (character === '"') {
          quoted = true;
        } else if (character === "{") {
          depth += 1;
        } else if (character === "}") {
          depth -= 1;
          if (depth === 0) {
            try {
              return JSON.parse(trimmed.slice(start, index + 1));
            } catch {
              break;
            }
          }
        }
      }
    }
    throw new Error("The Copilot response was not valid JSON.");
  }
}

async function modelText(model, messages) {
  const response = await model.sendRequest(messages, {}, cancellation.token);
  let body = "";
  for await (const fragment of response.text) {
    body += fragment;
  }
  return body;
}

async function listFiles(relativePath) {
  const rootPath = relativePath ? relativeWorkspacePath(relativePath) : ".";
  const start = rootPath === "." ? workspaceRoot : workspaceUri(rootPath);
  const found = [];
  const visit = async (uri, prefix) => {
    if (found.length >= MAX_SEARCH_FILES) {
      return;
    }
    const entries = await vscode.workspace.fs.readDirectory(uri);
    for (const [name, type] of entries) {
      if (IGNORED_DIRECTORIES.has(name)) {
        continue;
      }
      const nextRelative = prefix ? `${prefix}/${name}` : name;
      if (type === vscode.FileType.Directory) {
        await visit(vscode.Uri.joinPath(uri, name), nextRelative);
      } else if (type === vscode.FileType.File) {
        found.push(nextRelative);
      }
      if (found.length >= MAX_SEARCH_FILES) {
        return;
      }
    }
  };
  await visit(start, rootPath === "." ? "" : rootPath);
  return found;
}

async function searchFiles(query, relativePath) {
  const queryText = String(query || "").trim();
  if (!queryText) {
    throw new Error("Search query cannot be empty.");
  }
  const files = await listFiles(relativePath || "");
  const hits = [];
  for (const file of files) {
    if (hits.length >= 60) {
      break;
    }
    try {
      const text = await readText(workspaceUri(file), 120_000);
      const lines = text.split(/\r?\n/);
      lines.forEach((line, index) => {
        if (hits.length < 60 && line.toLowerCase().includes(queryText.toLowerCase())) {
          hits.push({ path: file, line: index + 1, text: line.slice(0, 500) });
        }
      });
    } catch {
      // Ignore non-text or inaccessible files during a bounded search.
    }
  }
  return hits;
}

async function applyChanges(changes) {
  if (!Array.isArray(changes) || !changes.length) {
    throw new Error("The apply action did not include any changes.");
  }
  const changedFiles = [];
  for (const change of changes) {
    const path = relativeWorkspacePath(change.path);
    const operation = String(change.operation || "replace").toLowerCase();
    const uri = workspaceUri(path);
    if (operation === "create") {
      if (await pathExists(uri)) {
        throw new Error(`Refusing to create existing file: ${path}`);
      }
      await vscode.workspace.fs.createDirectory(parentUri(uri));
      await vscode.workspace.fs.writeFile(uri, Buffer.from(String(change.content || ""), "utf8"));
    } else if (operation === "replace") {
      const oldText = String(change.old_text ?? change.oldText ?? "");
      const newText = String(change.new_text ?? change.newText ?? "");
      if (!oldText) {
        throw new Error(`Replace action requires old_text for ${path}.`);
      }
      const existing = await readText(uri, 2_000_000);
      const occurrences = existing.split(oldText).length - 1;
      if (occurrences !== 1) {
        throw new Error(`Replace precondition failed for ${path}: expected one matching old_text, found ${occurrences}.`);
      }
      await vscode.workspace.fs.writeFile(uri, Buffer.from(existing.replace(oldText, newText), "utf8"));
    } else {
      throw new Error(`Unsupported edit operation '${operation}' for ${path}.`);
    }
    changedFiles.push(path);
  }
  return changedFiles;
}

function instructionEntriesFromIndex(indexText) {
  const entries = [];
  for (const line of String(indexText || "").split(/\r?\n/)) {
    const match = line.match(/^\|\s*`?([^`|]+?)`?\s*\|\s*`?([^`|]+?)`?\s*\|\s*$/);
    if (!match || match[1].trim().toLowerCase() === "original path") {
      continue;
    }
    const originalPath = match[1].trim();
    const packagedPath = match[2].trim();
    if (originalPath && packagedPath && !/^[-:]+$/.test(originalPath) && !/^[-:]+$/.test(packagedPath)) {
      entries.push({ originalPath, packagedPath });
    }
  }
  return entries;
}

async function collectPackageContext(packagePath) {
  const promptUri = packageUri(packagePath, "prompt.md");
  const prompt = await readText(promptUri, 240_000);
  const extras = [];
  const required = [
    "repo-instructions/index.md",
    "capture/INSTRUCTIONS.md",
    "capture/summary.md",
    "reference-docs/index.md",
  ];
  for (const relativePath of required) {
    const uri = packageUri(packagePath, relativePath);
    if (await pathExists(uri)) {
      extras.push({ path: relativePath, content: await readText(uri, MAX_FILE_BYTES) });
    }
  }
  const instructionIndex = extras.find((item) => item.path === "repo-instructions/index.md");
  const instructionEntries = instructionIndex ? instructionEntriesFromIndex(instructionIndex.content) : [];
  for (const entry of instructionEntries.slice(0, 32)) {
    const uri = packageUri(packagePath, entry.packagedPath);
    if (await pathExists(uri)) {
      extras.push({ path: entry.packagedPath, content: await readText(uri, MAX_FILE_BYTES) });
    }
  }
  return { prompt, extras, instructionEntries };
}

function instructionPathsFromIndex(indexText) {
  return instructionEntriesFromIndex(indexText).map((entry) => entry.originalPath);
}

function isAutomationArtifact(path) {
  return path === ".automation-context" || path.startsWith(".automation-context/");
}

function agentRequest(context, history) {
  const instruction = [
    "You are an autonomous documentation-update executor running inside a controlled VS Code workspace.",
    "Return exactly one JSON object. Do not use Markdown fences or prose outside JSON.",
    "You can choose one action: read, list, search, apply, finish.",
    "read: {action:'read', paths:['repo/relative/path.md']}; list: {action:'list', path:'optional/relative/dir'}; search: {action:'search', query:'text', path:'optional/relative/dir'}.",
    "apply: {action:'apply', changes:[{path:'repo/relative/file.md', operation:'replace', old_text:'exact existing text', new_text:'replacement'}]} or operation:'create' with content.",
    "finish: {action:'finish', green_light:true|false, summary:'...', final_report:'...', spec_references:[], validation:[], reviewer_notes:[], prs_reviewed:[], diffs_reviewed:[], work_items_reviewed:[] }.",
    "Read repository instructions and captured evidence before editing. Keep changes minimal and documentation-focused.",
    "Never request shell commands, network access, Git actions, or edits outside the workspace. Do not edit .automation-context; the bridge writes the result artifact itself. Use exact old_text when applying a replacement.",
    "If no accurate documentation change is needed, finish with green_light false and explain why.",
  ].join("\n");
  const initial = [
    instruction,
    "\nPrepared handoff:\n",
    context.prompt,
    ...context.extras.map((extra) => `\n\nMandatory package file: ${extra.path}\n${extra.content}`),
  ].join("");
  return [
    vscode.LanguageModelChatMessage.User(initial),
    ...history.map((entry) => vscode.LanguageModelChatMessage.User(entry)),
  ];
}

async function runJob(jobUri) {
  const packagePath = parentUri(jobUri);
  const key = packagePath.toString();
  if (processing.has(key)) {
    return;
  }
  processing.add(key);
  let job;
  try {
    job = JSON.parse(await readText(jobUri));
    const expectedWorkspace = String(job.workspace_path || "");
    if (expectedWorkspace && expectedWorkspace !== workspaceRoot.fsPath) {
      if (job.dispatch_only) {
        const stateUri = packageUri(packagePath, JOB_STATE_FILE);
        const jobState = await readJson(stateUri);
        if (jobState.status === "dispatched") {
          return;
        }
        const branch = String(job.branch_name || "queued work item");
        await writeJson(stateUri, {
          status: "dispatching",
          branch_name: branch,
          target_workspace: expectedWorkspace,
          requested_at: now(),
        });
        await writeBridgeStatus("dispatching_new_window", {
          branch_name: branch,
          target_workspace: expectedWorkspace,
          detail: "Opening an isolated VS Code worktree for the queued work item.",
        });
        log(`Opening isolated VS Code window for ${branch} at ${expectedWorkspace}.`);
        const targetWorkspace = workspaceRoot.with({ path: expectedWorkspace });
        await vscode.commands.executeCommand("vscode.openFolder", targetWorkspace, {
          forceNewWindow: true,
          forceReuseWindow: false,
        });
        await writeJson(stateUri, {
          status: "dispatched",
          branch_name: branch,
          target_workspace: expectedWorkspace,
          dispatched_at: now(),
        });
        await writeBridgeStatus("waiting_for_isolated_window", {
          branch_name: branch,
          target_workspace: expectedWorkspace,
          detail: "The isolated worktree window was requested. Its bridge will run the queued work item.",
        });
        return;
      }
      throw new Error(`Queued job targets '${expectedWorkspace}', but the active workspace is '${workspaceRoot.fsPath}'.`);
    }
    const stateUri = packageUri(packagePath, JOB_STATE_FILE);
    const jobState = await readJson(stateUri);
    const branch = String(job.branch_name || "");
    if (!branch) {
      throw new Error("Queued bridge job does not contain a branch name.");
    }

    if (jobState.status === "running" && jobState.executor_id && jobState.executor_id !== bridgeInstanceId) {
      log(`Job for ${branch} is already being executed by another VS Code window.`);
      return;
    }

    if (job.open_new_window && !jobState.window_open_requested) {
      await writeJson(stateUri, {
        status: "opening_new_window",
        window_open_requested: true,
        requested_at: now(),
        branch_name: branch,
      });
      await writeBridgeStatus("opening_new_window", { branch_name: branch });
      log(`Requesting a new VS Code window for ${branch}.`);
      try {
        await vscode.commands.executeCommand("vscode.openFolder", workspaceRoot, {
          forceNewWindow: true,
          forceReuseWindow: false,
        });
      } catch (openError) {
        const message = openError instanceof Error ? openError.message : String(openError);
        log(`Could not open a separate VS Code window: ${message}. Continuing through the active bridge window.`);
      }
    }

    await writeJson(stateUri, {
      status: "running",
      started_at: now(),
      branch_name: branch,
      executor_id: bridgeInstanceId,
      window_open_requested: Boolean(job.open_new_window || jobState.window_open_requested || jobState.window_opened),
    });
    await writeBridgeStatus("running", {
      branch_name: branch,
      job_path: jobUri.fsPath,
      executor_id: bridgeInstanceId,
      detail: "The VS Code Copilot bridge is processing the queued work item.",
    });
    log(`Running bridge job for ${branch}.`);

    await writeBridgeStatus("waiting_for_model", {
      branch_name: branch,
      detail: "Waiting for VS Code Copilot to authorize and provide the configured language model.",
    });
    const model = await selectConfiguredModel(job.model_name);
    await writeBridgeStatus("running", {
      branch_name: branch,
      job_path: jobUri.fsPath,
      executor_id: bridgeInstanceId,
      model: describeModel(model),
      detail: "The VS Code Copilot bridge is processing the queued work item.",
    });
    const context = await collectPackageContext(packagePath);
    const instructionIndex = context.extras.find((item) => item.path === "repo-instructions/index.md");
    const instructionFilesRead = instructionIndex ? instructionPathsFromIndex(instructionIndex.content) : [];
    const captureFilesRead = context.extras.filter((item) => item.path.startsWith("capture/")).map((item) => item.path);
    const history = [];
    const changedFiles = [];
    const maxIterations = vscode.workspace.getConfiguration("contentAiPipelineBridge").get("maxIterations", 14);

    for (let iteration = 1; iteration <= maxIterations; iteration += 1) {
      const raw = await modelText(model, agentRequest(context, history));
      const decision = extractJson(raw);
      const action = String(decision.action || "").toLowerCase();
      if (action === "read") {
        try {
          const paths = Array.isArray(decision.paths) ? decision.paths.slice(0, 10) : [];
          const files = [];
          for (const path of paths) {
            const cleanPath = relativeWorkspacePath(path);
            files.push({ path: cleanPath, content: await readText(workspaceUri(cleanPath)) });
          }
          history.push(`Tool result for read:\n${JSON.stringify(files)}`);
        } catch (toolError) {
          history.push(`Tool error for read: ${toolError instanceof Error ? toolError.message : String(toolError)}`);
        }
        continue;
      }
      if (action === "list") {
        try {
          const files = await listFiles(String(decision.path || ""));
          history.push(`Tool result for list:\n${JSON.stringify(files)}`);
        } catch (toolError) {
          history.push(`Tool error for list: ${toolError instanceof Error ? toolError.message : String(toolError)}`);
        }
        continue;
      }
      if (action === "search") {
        try {
          const hits = await searchFiles(String(decision.query || ""), String(decision.path || ""));
          history.push(`Tool result for search:\n${JSON.stringify(hits)}`);
        } catch (toolError) {
          history.push(`Tool error for search: ${toolError instanceof Error ? toolError.message : String(toolError)}`);
        }
        continue;
      }
      if (action === "apply") {
        try {
          const applied = await applyChanges(decision.changes);
          applied.forEach((path) => {
            if (!changedFiles.includes(path)) {
              changedFiles.push(path);
            }
          });
          history.push(`Tool result for apply: ${JSON.stringify({ applied })}`);
        } catch (toolError) {
          history.push(`Tool error for apply: ${toolError instanceof Error ? toolError.message : String(toolError)}`);
        }
        continue;
      }
      if (action === "finish") {
        const publishableChangedFiles = changedFiles.filter((path) => !isAutomationArtifact(path));
        const result = {
          status: "completed",
          green_light: Boolean(decision.green_light) && publishableChangedFiles.length > 0,
          summary: String(decision.summary || "VS Code Copilot bridge completed the job."),
          changed_files: publishableChangedFiles,
          final_report: String(decision.final_report || decision.summary || "No final report was provided by the VS Code Copilot bridge."),
          spec_references: Array.isArray(decision.spec_references) ? decision.spec_references : [],
          validation: Array.isArray(decision.validation) ? decision.validation : [],
          instruction_files_read: instructionFilesRead,
          capture_files_read: captureFilesRead,
          prs_reviewed: Array.isArray(decision.prs_reviewed) ? decision.prs_reviewed : [],
          diffs_reviewed: Array.isArray(decision.diffs_reviewed) ? decision.diffs_reviewed : [],
          work_items_reviewed: Array.isArray(decision.work_items_reviewed) ? decision.work_items_reviewed : [],
          reviewer_notes: Array.isArray(decision.reviewer_notes) ? decision.reviewer_notes : [],
          provider: "vscode_bridge",
          model: describeModel(model),
          completed_at: now(),
        };
        await writeJson(packageUri(packagePath, RESULT_FILE), result);
        await writeJson(packageUri(packagePath, JOB_STATE_FILE), { status: "completed", completed_at: now(), branch_name: branch, changed_files: publishableChangedFiles });
        await writeBridgeStatus("ready", { last_completed_branch: branch, model: describeModel(model) });
        log(`Completed bridge job for ${branch}; ${publishableChangedFiles.length} publishable file(s) changed.`);
        return;
      }
      throw new Error(`Unsupported bridge action '${action}'.`);
    }
    throw new Error(`The Copilot bridge reached its ${maxIterations}-iteration limit without a finish action.`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    log(`Bridge job failed: ${message}`);
    const result = {
      status: "error",
      green_light: false,
      summary: "VS Code Copilot bridge failed before completing the job.",
      changed_files: [],
      final_report: "",
      spec_references: [],
      validation: [],
      instruction_files_read: [],
      capture_files_read: [],
      prs_reviewed: [],
      diffs_reviewed: [],
      work_items_reviewed: [],
      reviewer_notes: [],
      provider: "vscode_bridge",
      error: message,
      completed_at: now(),
    };
    try {
      await writeJson(packageUri(packagePath, RESULT_FILE), result);
      await writeJson(packageUri(packagePath, JOB_STATE_FILE), { status: "error", completed_at: now(), error: message });
      await writeBridgeStatus("error", { error: message });
    } catch (writeError) {
      log(`Could not persist bridge failure: ${writeError instanceof Error ? writeError.message : String(writeError)}`);
    }
  } finally {
    processing.delete(key);
  }
}

async function runPendingJobs() {
  if (!workspaceRoot || !vscode.workspace.getConfiguration("contentAiPipelineBridge").get("enabled", true)) {
    return;
  }
  const root = vscode.Uri.joinPath(workspaceRoot, CONTEXT_ROOT);
  if (!(await pathExists(root))) {
    await writeBridgeStatus("ready", { detail: "No queued Content AI jobs." });
    return;
  }
  const entries = await vscode.workspace.fs.readDirectory(root);
  for (const [name, type] of entries) {
    if (type !== vscode.FileType.Directory) {
      continue;
    }
    const jobUri = packageUri(vscode.Uri.joinPath(root, name), JOB_FILE);
    const resultUri = packageUri(vscode.Uri.joinPath(root, name), RESULT_FILE);
    if ((await pathExists(jobUri)) && !(await pathExists(resultUri))) {
      await runJob(jobUri);
    }
  }
}

function activate(context) {
  output = vscode.window.createOutputChannel("Content AI Pipeline Bridge");
  cancellation = new vscode.CancellationTokenSource();
  workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!workspaceRoot) {
    log("No workspace is open; Content AI bridge is inactive.");
    return;
  }

  context.subscriptions.push(output, cancellation);
  context.subscriptions.push(vscode.commands.registerCommand("cmfContentAiPipelineBridge.enableCopilot", enableCopilotBridge));
  context.subscriptions.push(vscode.commands.registerCommand("cmfContentAiPipelineBridge.runPendingJobs", runPendingJobs));
  context.subscriptions.push(vscode.commands.registerCommand("cmfContentAiPipelineBridge.showStatus", () => output.show(true)));
  const root = vscode.Uri.joinPath(workspaceRoot, CONTEXT_ROOT);
  const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root, `*/${JOB_FILE}`));
  context.subscriptions.push(watcher);
  watcher.onDidCreate((uri) => void runJob(uri));
  watcher.onDidChange((uri) => void runJob(uri));
  void checkCopilotAccess().then((access) => {
    if (access.status !== "ready") {
      void vscode.window.showWarningMessage(
        "Content AI requires one-time VS Code Copilot consent before it can process pipeline jobs.",
        "Enable Copilot Bridge",
      ).then((selection) => {
        if (selection === "Enable Copilot Bridge") {
          void enableCopilotBridge();
        }
      });
      return;
    }
    void runPendingJobs();
  });
  log(`Content AI bridge activated for ${workspaceRoot.fsPath}.`);
}

function deactivate() {
  cancellation?.cancel();
}

module.exports = { activate, deactivate };
