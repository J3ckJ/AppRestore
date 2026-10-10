// Tauri candidate: empty window + Python core as a sidecar process.
// The sidecar is spawned at startup (in parallel with the WebView) and
// answers with one JSON line. Marks go to the file named by BENCH_OUT.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

fn now_ms() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64() * 1000.0
}

#[derive(Default)]
struct Marks {
    values: serde_json::Map<String, serde_json::Value>,
    core_done: bool,
}

struct Bench(Arc<(Mutex<Marks>, Condvar)>);

fn sidecar_path() -> PathBuf {
    let dir = std::env::current_exe().unwrap().parent().unwrap().to_path_buf();
    let exe = if cfg!(windows) { "apprestore-core.exe" } else { "apprestore-core" };
    // onedir layout: <dir>/apprestore-core/apprestore-core ; onefile: <dir>/apprestore-core
    let nested = dir.join("apprestore-core").join(exe);
    if nested.is_file() { nested } else { dir.join(exe) }
}

fn spawn_sidecar(state: Arc<(Mutex<Marks>, Condvar)>) {
    std::thread::spawn(move || {
        let mut cmd = Command::new(sidecar_path());
        cmd.stdout(Stdio::piped()).stdin(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let mut line = String::new();
        let mut error = None;
        match cmd.spawn() {
            Ok(mut child) => {
                let mut reader = BufReader::new(child.stdout.take().unwrap());
                if reader.read_line(&mut line).is_err() || line.trim().is_empty() {
                    error = Some("no answer from sidecar".to_string());
                }
                let _ = child.wait();
            }
            Err(e) => error = Some(format!("spawn failed: {e}")),
        }
        let (lock, cvar) = &*state;
        let mut marks = lock.lock().unwrap();
        marks.values.insert("core_answer".into(), now_ms().into());
        if let Ok(serde_json::Value::Object(extra)) = serde_json::from_str(line.trim()) {
            for (k, v) in extra { marks.values.insert(k, v); }
        }
        if let Some(e) = error { marks.values.insert("error".into(), e.into()); }
        marks.core_done = true;
        cvar.notify_all();
    });
}

#[tauri::command]
fn mark_window(bench: tauri::State<Bench>) {
    let (lock, _) = &*bench.0;
    lock.lock().unwrap().values.insert("window_shown".into(), now_ms().into());
}

#[tauri::command]
async fn wait_core(bench: tauri::State<'_, Bench>) -> Result<(), String> {
    let state = bench.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let (lock, cvar) = &*state;
        let mut marks = lock.lock().unwrap();
        while !marks.core_done { marks = cvar.wait(marks).unwrap(); }
    })
    .await
    .map_err(|e| e.to_string())
}

#[tauri::command]
fn finish(app: tauri::AppHandle, bench: tauri::State<Bench>) {
    if let Ok(path) = std::env::var("BENCH_OUT") {
        let (lock, _) = &*bench.0;
        let marks = lock.lock().unwrap();
        let _ = std::fs::write(path, serde_json::Value::Object(marks.values.clone()).to_string());
        app.exit(0);
    }
}

fn main() {
    let state = Arc::new((Mutex::new(Marks::default()), Condvar::new()));
    state.0.lock().unwrap().values.insert("process_start".into(), now_ms().into());
    spawn_sidecar(state.clone());
    tauri::Builder::default()
        .manage(Bench(state))
        .invoke_handler(tauri::generate_handler![mark_window, wait_core, finish])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
