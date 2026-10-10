// Electron candidate: empty window + Python core as a sidecar process.
const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const marks = { process_start: Date.now() };
const exe = process.platform === "win32" ? "apprestore-core.exe" : "apprestore-core";
const sidecar = path.join(process.resourcesPath, "apprestore-core", exe);

const core = new Promise((resolve) => {
  let out = "";
  const child = spawn(sidecar, [], { stdio: ["ignore", "pipe", "ignore"], windowsHide: true });
  child.stdout.on("data", (chunk) => {
    out += chunk;
    if (out.includes("\n")) {
      marks.core_answer = Date.now();
      try { Object.assign(marks, JSON.parse(out.split("\n")[0])); } catch (e) { marks.error = String(e); }
      resolve();
    }
  });
  child.on("error", (e) => { marks.core_answer = Date.now(); marks.error = String(e); resolve(); });
  child.on("exit", () => { if (!marks.core_answer) { marks.core_answer = Date.now(); marks.error = "no answer"; resolve(); } });
});

app.whenReady().then(() => {
  const win = new BrowserWindow({ width: 960, height: 640, show: false, title: "AppRestore engine bench (Electron)" });
  win.once("ready-to-show", async () => {
    win.show();
    marks.window_shown = Date.now();
    await core;
    if (process.env.BENCH_OUT) {
      fs.writeFileSync(process.env.BENCH_OUT, JSON.stringify(marks));
      app.quit();
    }
  });
  win.loadFile(path.join(__dirname, "index.html"));
});
