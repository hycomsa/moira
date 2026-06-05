// Moira desktop shell (Tauri v2).
//
// Architecture (matches the verified headless setup): the Python sidecar serves
// BOTH the cockpit frontend and the /api on http://127.0.0.1:8765. The window
// loads that URL — one origin, no embedded-asset vs dev-server ambiguity, no CORS.
// We WAIT for the sidecar to be listening before opening the window, so the user
// never sees "connection refused".

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

const SIDECAR_ADDR: &str = "127.0.0.1:8765";

struct Sidecar(Mutex<Option<Child>>);

fn spawn_sidecar() -> Option<Child> {
    // run from the app's working dir (moira-app/). The sidecar serves the built
    // cockpit (--static) and reads the AI SDLC repo (default --repo).
    let candidates = [
        ("orchestrator/moira_api.py", "cockpit/dist"),
        ("../orchestrator/moira_api.py", "../cockpit/dist"),
    ];
    for (script, static_dir) in candidates {
        if std::path::Path::new(script).exists() {
            match Command::new("python3")
                .args([script, "--port", "8765", "--static", static_dir])
                .spawn()
            {
                Ok(child) => {
                    println!("[moira] sidecar started: {script}");
                    return Some(child);
                }
                Err(e) => eprintln!("[moira] failed to start sidecar {script}: {e}"),
            }
        }
    }
    eprintln!("[moira] sidecar script not found from cwd {:?}", std::env::current_dir());
    None
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            use tauri::Manager;
            let child = spawn_sidecar();
            *app.state::<Sidecar>().0.lock().unwrap() = child;

            if wait_for_sidecar(Duration::from_secs(25)) {
                println!("[moira] sidecar is listening — opening window");
            } else {
                eprintln!("[moira] sidecar did not come up in time — opening window anyway");
            }

            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(
                    "http://127.0.0.1:8765".parse().expect("valid url"),
                ),
            )
            .title("Moira — AI-native SDLC cockpit")
            .inner_size(1440.0, 900.0)
            .min_inner_size(1100.0, 700.0)
            .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                use tauri::Manager;
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
