const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const { spawnSync } = require("child_process");

const outDir = __dirname;
const edgePath = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const css = `
  :root {
    --bg: #0f1117;
    --panel: #1e1f28;
    --panel2: #252837;
    --line: #3b4054;
    --text: #e7e9ef;
    --muted: #aab1c3;
    --blue: #0b63ce;
    --green: #2ea043;
    --yellow: #d29922;
    --red: #f85149;
    --purple: #a371f7;
  }
  * { box-sizing: border-box; }
  body {
    width: 1600px;
    height: 900px;
    margin: 0;
    overflow: hidden;
    background: #f5f7fa;
    color: #16202b;
    font-family: "Segoe UI", Arial, sans-serif;
  }
  .slide {
    width: 1600px;
    height: 900px;
    padding: 44px;
    background: linear-gradient(180deg, #f8fafc 0%, #edf2f7 100%);
  }
  .title {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 24px;
  }
  .title h1 {
    margin: 0;
    font-size: 38px;
    letter-spacing: 0;
  }
  .title p {
    margin: 8px 0 0;
    color: #586475;
    font-size: 18px;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 38px;
    padding: 0 14px;
    border-radius: 6px;
    background: #e8f0fb;
    color: #0b63ce;
    border: 1px solid #c8d9f2;
    font-weight: 700;
  }
  .window {
    border: 1px solid #cfd7e2;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 18px 50px rgba(20, 30, 45, 0.18);
    background: var(--bg);
  }
  .vscode-bar {
    height: 44px;
    background: #181a22;
    border-bottom: 1px solid #2e3240;
    display: flex;
    align-items: center;
    padding: 0 18px;
    color: var(--muted);
    font-size: 14px;
  }
  .traffic {
    display: flex;
    gap: 8px;
    margin-right: 18px;
  }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: #3b4054; }
  .dot.red { background: #f85149; }
  .dot.yellow { background: #d29922; }
  .dot.green { background: #2ea043; }
  .vscode {
    height: 720px;
    display: grid;
    grid-template-columns: 72px 330px 1fr;
    background: #11131a;
    color: var(--text);
  }
  .activity {
    background: #151821;
    border-right: 1px solid #2e3240;
    padding: 18px 16px;
    display: flex;
    flex-direction: column;
    gap: 22px;
    color: var(--muted);
    font-size: 24px;
    align-items: center;
  }
  .sidebar {
    background: #1b1e29;
    border-right: 1px solid #2e3240;
    padding: 18px;
  }
  .side-title {
    color: var(--muted);
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    margin-bottom: 14px;
  }
  .file {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 31px;
    padding: 5px 7px;
    border-radius: 5px;
    color: #d4d8e4;
    font-size: 14px;
  }
  .file.active { background: #283044; color: #fff; }
  .file .icon { color: #7aa2f7; width: 18px; text-align: center; }
  .main {
    display: grid;
    grid-template-rows: 48px 1fr;
    min-width: 0;
  }
  .tabbar {
    background: #1b1e29;
    border-bottom: 1px solid #2e3240;
    display: flex;
    align-items: center;
    padding-left: 14px;
    gap: 8px;
  }
  .tab {
    height: 36px;
    display: inline-flex;
    align-items: center;
    padding: 0 14px;
    border-radius: 6px 6px 0 0;
    background: #252837;
    color: var(--text);
    font-size: 14px;
  }
  .content {
    display: grid;
    grid-template-columns: 1fr 460px;
    min-height: 0;
  }
  .editor {
    padding: 26px 30px;
    border-right: 1px solid #2e3240;
    overflow: hidden;
  }
  .chat {
    background: #141720;
    padding: 20px;
    overflow: hidden;
  }
  .chat-title {
    display: flex;
    justify-content: space-between;
    color: #fff;
    font-weight: 700;
    margin-bottom: 16px;
  }
  .message {
    background: #252837;
    border: 1px solid #3b4054;
    border-radius: 8px;
    padding: 16px;
    color: #e7e9ef;
    font-size: 15px;
    line-height: 1.45;
    margin-bottom: 14px;
  }
  .message strong { color: #fff; }
  .agent {
    border-left: 4px solid var(--blue);
  }
  .agent.done {
    border-left-color: var(--green);
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }
  .chip {
    padding: 5px 9px;
    border: 1px solid #46506a;
    border-radius: 999px;
    background: #1d2230;
    color: #b9c2d6;
    font-size: 12px;
  }
  .code {
    background: #0c0f16;
    border: 1px solid #2e3240;
    border-radius: 8px;
    padding: 18px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 15px;
    line-height: 1.55;
    color: #d8dee9;
    white-space: pre-wrap;
  }
  .code .key { color: #7aa2f7; }
  .code .str { color: #9ece6a; }
  .code .comment { color: #8992a8; }
  .checklist {
    display: grid;
    gap: 10px;
    margin-top: 14px;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 10px;
    color: #dce2ee;
    font-size: 15px;
  }
  .check::before {
    content: "";
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--green);
  }
  .context-grid {
    height: 720px;
    display: grid;
    grid-template-columns: 430px 1fr;
  }
  .context-sidebar {
    padding: 24px;
    background: #1b1e29;
    border-right: 1px solid #2e3240;
  }
  .context-preview {
    padding: 28px;
    background: #10131a;
  }
  .folder-title {
    font-size: 16px;
    color: #fff;
    font-weight: 700;
    margin-bottom: 18px;
  }
  .tree-line {
    min-height: 34px;
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 5px 8px;
    border-radius: 6px;
    color: #d4d8e4;
    font-size: 15px;
  }
  .tree-line.level1 { padding-left: 28px; }
  .tree-line.active { background: #283044; }
  .callout {
    margin-top: 18px;
    padding: 16px;
    background: #f7fbff;
    border: 1px solid #c8d9f2;
    border-radius: 8px;
    color: #253246;
    font-size: 16px;
    line-height: 1.45;
  }
`;

function page(title, subtitle, badge, inner) {
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>${title}</title>
  <style>${css}</style>
</head>
<body>
  <div class="slide">
    <div class="title">
      <div>
        <h1>${title}</h1>
        <p>${subtitle}</p>
      </div>
      <div class="badge">${badge}</div>
    </div>
    ${inner}
  </div>
</body>
</html>`;
}

const handoff = page(
  "Agent Handoff",
  "The dashboard opens the work branch and sends a complete context package to the configured AI provider.",
  "TFS Autonomous Change Pipeline",
  `
  <div class="window">
    <div class="vscode-bar">
      <div class="traffic"><span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span></div>
      Visual Studio Code - WSL: Ubuntu - DocumentationPortal-#01
    </div>
    <div class="vscode">
      <div class="activity"><span>F</span><span>S</span><span>B</span><span>C</span></div>
      <div class="sidebar">
        <div class="side-title">Explorer</div>
        <div class="file active"><span class="icon">M</span> prompt.md</div>
        <div class="file"><span class="icon">M</span> work-item.md</div>
        <div class="file"><span class="icon">{}</span> work-item.json</div>
        <div class="file"><span class="icon">H</span> description.html</div>
        <div class="file"><span class="icon">{}</span> agent-result.json</div>
        <div class="file"><span class="icon">D</span> repo-instructions/index.md</div>
      </div>
      <div class="main">
        <div class="tabbar"><div class="tab">prompt.md</div><div class="tab">work-item.md</div></div>
        <div class="content">
          <div class="editor">
            <div class="code"><span class="comment"># Generated prompt</span>
Use the attached work item package and repository instructions to implement the required change on the current branch.

Branch:
12.0/feature/152658-doc-data-dictionary...

Read:
- work-item.md
- description.html
- repo-instructions/index.md

When finished, write:
.git/copilot-context/.../agent-result.json</div>
            <div class="callout">The dashboard controls branch, context, validation, push, and PR creation. The agent only edits files and returns a structured result.</div>
          </div>
          <div class="chat">
            <div class="chat-title"><span>CHAT</span><span>CM GPT</span></div>
            <div class="message"><strong>Dashboard input</strong><br><br>Use the attached work item package and repository instructions to implement the required documentation updates on the current branch.</div>
            <div class="message agent"><strong>CM GPT Documentation Automation</strong><br>Reading context files and inspecting the repository.</div>
            <div class="chips"><span class="chip">work-item.md</span><span class="chip">prompt.md</span><span class="chip">repo instructions</span><span class="chip">current branch</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>`
);

const output = page(
  "Agent Output",
  "After editing the branch, the agent reports a green-light result that the dashboard validates independently.",
  "Structured Result",
  `
  <div class="window">
    <div class="vscode-bar">
      <div class="traffic"><span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span></div>
      Visual Studio Code - Agent completed
    </div>
    <div class="vscode">
      <div class="activity"><span>F</span><span>S</span><span>B</span><span>C</span></div>
      <div class="sidebar">
        <div class="side-title">Changed Files</div>
        <div class="file active"><span class="icon">M</span> experiment_data_dictionary.md</div>
        <div class="file"><span class="icon">M</span> experiment/index.md</div>
        <div class="file"><span class="icon">M</span> experiment-definition/index.md</div>
        <div class="file"><span class="icon">M</span> experimentmanagement.md</div>
      </div>
      <div class="main">
        <div class="tabbar"><div class="tab">agent-result.json</div><div class="tab">Final report</div></div>
        <div class="content">
          <div class="editor">
            <div class="code">{
  <span class="key">"status"</span>: <span class="str">"completed"</span>,
  <span class="key">"green_light"</span>: true,
  <span class="key">"summary"</span>: <span class="str">"Added Experiment data dictionary updates."</span>,
  <span class="key">"changed_files"</span>: [
    <span class="str">"docs/userguide/.../experiment_data_dictionary.md"</span>,
    <span class="str">"docs/tutorials/.../experimentmanagement.md"</span>
  ],
  <span class="key">"instruction_files_read"</span>: [
    <span class="str">".agents/skills/style-guide-validator/SKILL.md"</span>
  ],
  <span class="key">"spec_references"</span>: [<span class="str">"CMF162043"</span>, <span class="str">"CMF162044"</span>]
}</div>
          </div>
          <div class="chat">
            <div class="chat-title"><span>CHAT</span><span>Completed</span></div>
            <div class="message agent done"><strong>Documentation update completed</strong><br><br>Edited data dictionary content, updated object model diagrams, and prepared a reviewer-ready summary.</div>
            <div class="checklist">
              <div class="check">Agent wrote agent-result.json</div>
              <div class="check">Dashboard checks repository instructions</div>
              <div class="check">Dashboard validates Markdown and links</div>
              <div class="check">Only then can push and Draft PR happen</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>`
);

const context = page(
  "Context Package",
  "Each work item receives a reproducible package with all inputs required by the agent.",
  ".git/copilot-context",
  `
  <div class="window">
    <div class="vscode-bar">
      <div class="traffic"><span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span></div>
      DocumentationPortal-#01/.git/copilot-context/12.0-feature-152658...
    </div>
    <div class="context-grid">
      <div class="context-sidebar">
        <div class="folder-title">12.0-feature-152658...</div>
        <div class="tree-line active"><span class="icon">M</span> work-item.md</div>
        <div class="tree-line"><span class="icon">{}</span> work-item.json</div>
        <div class="tree-line"><span class="icon">H</span> description.html</div>
        <div class="tree-line"><span class="icon">M</span> prompt.md</div>
        <div class="tree-line"><span class="icon">{}</span> agent-result.json</div>
        <div class="tree-line"><span class="icon">L</span> agent-provider.log</div>
        <div class="tree-line"><span class="icon">D</span> repo-instructions/</div>
        <div class="tree-line level1"><span class="icon">M</span> index.md</div>
        <div class="tree-line level1"><span class="icon">M</span> AGENTS.md / .agents files</div>
      </div>
      <div class="context-preview">
        <div class="code"># Work item 152658

## Target repository
- Project: Product
- Repository: DocumentationPortal
- Base branch: 12.0/dev
- Effective branch: 12.0/feature/152658-...

## Work item metadata
- Title: Data Dictionary - Create Experiment by Assigning Multiple Materials
- Type: Task
- Parent: User Story
- Tags: 12.0

## Repository instructions
- Read repo-instructions/index.md
- Follow AGENTS.md and .agents materials

## Expected workflow
1. Inspect work item content and references.
2. Edit documentation on the current branch.
3. Write agent-result.json.</div>
        <div class="callout">The package makes every agent run traceable: input context, instructions, prompt, provider log, result file, and final report can all be reviewed later.</div>
      </div>
    </div>
  </div>`
);

const assets = [
  ["agent-handoff", handoff],
  ["agent-output", output],
  ["context-package", context],
];

for (const [name, html] of assets) {
  const htmlPath = path.join(outDir, `${name}.html`);
  fs.writeFileSync(htmlPath, html, "utf8");
}

if (!fs.existsSync(edgePath)) {
  throw new Error(`Microsoft Edge was not found at ${edgePath}`);
}

for (const [name] of assets) {
  const htmlPath = path.join(outDir, `${name}.html`);
  const pngPath = path.join(outDir, `${name}.png`);
  const result = spawnSync(edgePath, [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--allow-file-access-from-files",
    "--window-size=1600,900",
    `--screenshot=${pngPath}`,
    pathToFileURL(htmlPath).href,
  ], {
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `Failed to render ${name}`);
  }
  console.log(pngPath);
}
