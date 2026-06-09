// Moira desktop shell (Tauri v2).
//
// The Python orchestrator sidecar serves BOTH the cockpit frontend and the /api
// on http://127.0.0.1:8765; the window loads that URL (one origin, no CORS). We
// wait for the sidecar to listen before opening the window.
//
//   dev   (debug build): runs the Python source sidecar (orchestrator/moira_api.py)
//   release            : runs the bundled, PyInstaller-frozen sidecar binary
//                        (Tauri externalBin) placed next to the app executable —
//                        end users need no system Python.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;

const SIDECAR_ADDR: &str = "127.0.0.1:8765";

struct Sidecar(Mutex<Option<Child>>);

// Native OS folder picker, invoked from the cockpit (which loads the remote sidecar origin —
// IPC for it is enabled via the `remote` field in capabilities/default.json). Returns the chosen
// absolute path, or null if the user cancels. The JS side falls back to an in-app browser if this
// is unavailable (e.g. web mode).
#[tauri::command]
fn pick_folder(app: tauri::AppHandle, title: Option<String>) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let mut builder = app.dialog().file();
    if let Some(t) = title.filter(|t| !t.is_empty()) {
        builder = builder.set_title(t);
    }
    builder
        .blocking_pick_folder()
        .and_then(|fp| fp.into_path().ok())
        .map(|pb| pb.to_string_lossy().into_owned())
}

fn spawn_sidecar(app: &tauri::AppHandle) -> Option<Child> {
    let mut cmd = if cfg!(debug_assertions) {
        // dev: the Python source sidecar serving the freshly built cockpit dist
        let candidates = [
            ("orchestrator/moira_api.py", "cockpit/dist"),
            ("../orchestrator/moira_api.py", "../cockpit/dist"),
        ];
        let mut found: Option<Command> = None;
        for (script, static_dir) in candidates {
            if std::path::Path::new(script).exists() {
                let mut c = Command::new("python3");
                c.args([script, "--port", "8765", "--static", static_dir]);
                found = Some(c);
                break;
            }
        }
        match found {
            Some(c) => c,
            None => {
                eprintln!("[moira] dev sidecar script not found from {:?}", std::env::current_dir());
                return None;
            }
        }
    } else {
        // release: the bundled frozen sidecar binary sits next to the app executable
        let exe = std::env::current_exe().ok()?;
        let name = if cfg!(windows) { "moira-sidecar.exe" } else { "moira-sidecar" };
        let bin = exe.parent()?.join(name);
        let mut c = Command::new(&bin);
        c.args(["--port", "8765"]);
        // persist run/audit state in the OS app-data dir (not the unpredictable cwd)
        if let Ok(data) = app.path().app_data_dir() {
            let _ = std::fs::create_dir_all(&data);
            c.env("MOIRA_DB", data.join("moira.sqlite"));
        }
        c
    };
    match cmd.spawn() {
        Ok(child) => {
            println!("[moira] sidecar started");
            Some(child)
        }
        Err(e) => {
            eprintln!("[moira] failed to start sidecar: {e}");
            None
        }
    }
}

fn sidecar_listening() -> bool {
    SIDECAR_ADDR
        .parse::<SocketAddr>()
        .ok()
        .and_then(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok())
        .is_some()
}

fn wait_for_sidecar(max: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < max {
        if sidecar_listening() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

// Background update check (release only): if a newer signed release is published,
// ask the user, then download + install + relaunch. Driven from Rust because the
// window loads a remote (127.0.0.1) origin where JS plugin APIs aren't injected.
async fn check_for_update(app: tauri::AppHandle) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    use tauri_plugin_updater::UpdaterExt;

    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            eprintln!("[moira] updater unavailable: {e}");
            return;
        }
    };
    match updater.check().await {
        Ok(Some(update)) => {
            let version = update.version.clone();
            let install = app
                .dialog()
                .message(format!(
                    "Moira {version} is available. Download and install now? The app will restart."
                ))
                .title("Update available")
                .kind(MessageDialogKind::Info)
                .buttons(MessageDialogButtons::OkCancelCustom(
                    "Install".to_string(),
                    "Later".to_string(),
                ))
                .blocking_show();
            if install {
                match update.download_and_install(|_chunk, _total| {}, || {}).await {
                    Ok(_) => app.restart(),
                    Err(e) => eprintln!("[moira] update install failed: {e}"),
                }
            }
        }
        Ok(None) => println!("[moira] up to date"),
        Err(e) => eprintln!("[moira] update check failed: {e}"),
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![pick_folder])
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let child = spawn_sidecar(&handle);
            *app.state::<Sidecar>().0.lock().unwrap() = child;

            if wait_for_sidecar(Duration::from_secs(25)) {
                println!("[moira] sidecar is listening — opening window");
            } else {
                eprintln!("[moira] sidecar did not come up in time — opening window anyway");
            }

            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External("http://127.0.0.1:8765".parse().expect("valid url")),
            )
            .title("Moira — AI-native SDLC cockpit")
            .inner_size(1440.0, 900.0)
            .min_inner_size(1100.0, 700.0)
            .build()?;

            // check for updates in the background (no-op in dev / when no release feed)
            if !cfg!(debug_assertions) {
                tauri::async_runtime::spawn(check_for_update(handle));
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Sidecar>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Moira");
}
