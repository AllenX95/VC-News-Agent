use serde::Serialize;
use serde_json::Value;
use std::{
    fs::{create_dir_all, OpenOptions},
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, SystemTime},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const BASE_PORT: u16 = 8011;
const MAX_PORT: u16 = 8020;

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<Child>>,
    port: Mutex<Option<u16>>,
    owned: Mutex<bool>,
}

#[derive(Serialize)]
struct BackendLaunchResult {
    ok: bool,
    port: Option<u16>,
    base_url: Option<String>,
    owned: bool,
    message: String,
}

fn find_project_root(start: PathBuf) -> Option<PathBuf> {
    let mut cursor = if start.is_file() {
        start.parent()?.to_path_buf()
    } else {
        start
    };

    loop {
        if cursor.join("app.py").exists() && cursor.join("ai_agent").exists() {
            return Some(cursor);
        }
        if !cursor.pop() {
            return None;
        }
    }
}

fn root_dir() -> PathBuf {
    if let Ok(root) = std::env::var("VC_NEWS_ROOT") {
        let root = PathBuf::from(root);
        if root.join("app.py").exists() {
            return root;
        }
    }
    if let Ok(current) = std::env::current_dir() {
        if let Some(root) = find_project_root(current) {
            return root;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(root) = find_project_root(exe) {
            return root;
        }
    }
    PathBuf::from(".")
}

fn api_base(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn launcher_log_path(root: &PathBuf) -> PathBuf {
    root.join("logs").join("desktop-launcher.log")
}

fn append_launcher_log(root: &PathBuf, message: impl AsRef<str>) {
    let log_dir = root.join("logs");
    if create_dir_all(&log_dir).is_err() {
        return;
    }
    let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(launcher_log_path(root))
    else {
        return;
    };
    let _ = writeln!(file, "[{:?}] {}", SystemTime::now(), message.as_ref());
}

fn probe_backend_path(port: u16, path: &str) -> Result<(), String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(700))
        .map_err(|error| format!("connect {path}: {error}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(700)));

    let request =
        format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("write {path}: {error}"))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("read {path}: {error}"))?;

    let status_line = response.lines().next().unwrap_or("");
    if !status_line.contains(" 200 ") {
        return Err(format!("{path}: {status_line}"));
    }

    let body = response
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or("");
    let payload = serde_json::from_str::<Value>(body.trim())
        .map_err(|error| format!("json {path}: {error}"))?;
    if payload
        .get("app_id")
        .and_then(|value| value.as_str())
        .is_some_and(|app_id| app_id == "ai-investment-agent")
    {
        return Ok(());
    }

    Err(format!("{path}: unexpected app-info payload"))
}

fn probe_backend_detail(port: u16) -> Result<(), String> {
    let mut last_error = "no probe attempted".to_string();
    for path in ["/api/v1/app-info", "/api/app-info"] {
        match probe_backend_path(port, path) {
            Ok(()) => return Ok(()),
            Err(error) => {
                last_error = error;
            }
        }
    }
    Err(last_error)
}

fn probe_backend(port: u16) -> bool {
    probe_backend_detail(port).is_ok()
}

fn find_existing_backend() -> Option<u16> {
    (BASE_PORT..=MAX_PORT).find(|port| probe_backend(*port))
}

fn port_available(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn find_free_port() -> Option<u16> {
    (BASE_PORT..=MAX_PORT).find(|port| port_available(*port))
}

fn python_path(root: &PathBuf) -> PathBuf {
    if let Ok(path) = std::env::var("VC_NEWS_PYTHON") {
        return PathBuf::from(path);
    }
    #[cfg(windows)]
    {
        let candidate = root.join(".venv").join("Scripts").join("python.exe");
        if candidate.exists() {
            return candidate;
        }
    }
    #[cfg(not(windows))]
    {
        let candidate = root.join(".venv").join("bin").join("python");
        if candidate.exists() {
            return candidate;
        }
    }
    PathBuf::from("python")
}

fn backend_log_path(root: &PathBuf) -> PathBuf {
    root.join("logs").join("desktop-backend.log")
}

fn attach_backend_log(command: &mut Command, root: &PathBuf) {
    let log_dir = root.join("logs");
    if create_dir_all(&log_dir).is_err() {
        return;
    }

    let Ok(stdout) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(backend_log_path(root))
    else {
        return;
    };
    if let Ok(stderr) = stdout.try_clone() {
        command.stderr(Stdio::from(stderr));
    }
    command.stdout(Stdio::from(stdout));
}

fn spawn_backend_process(root: &PathBuf, port: u16) -> Result<Child, String> {
    if let Ok(exe) = std::env::var("VC_NEWS_BACKEND_EXE") {
        append_launcher_log(root, format!("spawning backend exe={} port={port}", exe));
        let mut command = Command::new(exe);
        command
            .arg("--port")
            .arg(port.to_string())
            .arg("--no-open-browser")
            .env("VC_NEWS_DISABLE_STARTUP_CATCHUP", "1")
            .current_dir(root);
        attach_backend_log(&mut command, root);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        return command.spawn().map_err(|error| error.to_string());
    }

    let app_py = root.join("app.py");
    if !app_py.exists() {
        return Err(format!("Cannot find backend entry: {}", app_py.display()));
    }

    let python = python_path(root);
    append_launcher_log(
        root,
        format!(
            "spawning python={} app={} port={port}",
            python.display(),
            app_py.display()
        ),
    );
    let mut command = Command::new(python);
    command
        .arg("-B")
        .arg(app_py)
        .arg("--port")
        .arg(port.to_string())
        .arg("--no-open-browser")
        .env("VC_NEWS_DISABLE_STARTUP_CATCHUP", "1")
        .current_dir(root);
    attach_backend_log(&mut command, root);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    command.spawn().map_err(|error| error.to_string())
}

fn stop_child(child: &mut Child) -> Result<(), String> {
    #[cfg(windows)]
    {
        let pid = child.id().to_string();
        let mut command = Command::new("taskkill");
        command.args(["/PID", &pid, "/T", "/F"]);
        command.creation_flags(0x08000000);
        return command
            .status()
            .map(|_| ())
            .map_err(|error| error.to_string());
    }

    #[cfg(not(windows))]
    {
        child.kill().map_err(|error| error.to_string())
    }
}

#[tauri::command]
fn ensure_backend(state: tauri::State<BackendState>) -> BackendLaunchResult {
    let root = root_dir();
    append_launcher_log(
        &root,
        format!(
            "ensure_backend root={} cwd={:?} exe={:?}",
            root.display(),
            std::env::current_dir().ok(),
            std::env::current_exe().ok()
        ),
    );

    if let Some(port) = find_existing_backend() {
        append_launcher_log(&root, format!("connected existing backend port={port}"));
        *state.port.lock().expect("port lock") = Some(port);
        *state.owned.lock().expect("owned lock") = false;
        return BackendLaunchResult {
            ok: true,
            port: Some(port),
            base_url: Some(api_base(port)),
            owned: false,
            message: format!("Connected to existing backend on port {port}"),
        };
    }

    let Some(port) = find_free_port() else {
        append_launcher_log(&root, "no free backend port in 8011-8020");
        return BackendLaunchResult {
            ok: false,
            port: None,
            base_url: None,
            owned: false,
            message: "Ports 8011-8020 are occupied".to_string(),
        };
    };

    append_launcher_log(&root, format!("selected backend port={port}"));
    let mut child = Some(match spawn_backend_process(&root, port) {
        Ok(child) => child,
        Err(error) => {
            append_launcher_log(&root, format!("spawn failed: {error}"));
            return BackendLaunchResult {
                ok: false,
                port: Some(port),
                base_url: Some(api_base(port)),
                owned: false,
                message: error,
            };
        }
    });

    let log_path = backend_log_path(&root);
    for attempt in 0..75 {
        std::thread::sleep(Duration::from_millis(400));
        if let Some(running_child) = child.as_mut() {
            match running_child.try_wait() {
                Ok(Some(status)) => {
                    append_launcher_log(
                        &root,
                        format!("backend child exited before ready status={status}"),
                    );
                    return BackendLaunchResult {
                        ok: false,
                        port: Some(port),
                        base_url: Some(api_base(port)),
                        owned: false,
                        message: format!(
                            "Backend process exited before startup ({status}). Log: {}",
                            log_path.display()
                        ),
                    };
                }
                Ok(None) => {}
                Err(error) => {
                    return BackendLaunchResult {
                        ok: false,
                        port: Some(port),
                        base_url: Some(api_base(port)),
                        owned: false,
                        message: format!(
                            "Cannot inspect backend process: {error}. Log: {}",
                            log_path.display()
                        ),
                    };
                }
            }
        }

        match probe_backend_detail(port) {
            Ok(()) => {
                append_launcher_log(
                    &root,
                    format!("backend probe succeeded port={port} attempt={attempt}"),
                );
                *state.child.lock().expect("child lock") = child.take();
                *state.port.lock().expect("port lock") = Some(port);
                *state.owned.lock().expect("owned lock") = true;
                return BackendLaunchResult {
                    ok: true,
                    port: Some(port),
                    base_url: Some(api_base(port)),
                    owned: true,
                    message: format!("Started backend on port {port}"),
                };
            }
            Err(error) => {
                if attempt % 10 == 0 {
                    append_launcher_log(
                        &root,
                        format!("backend probe pending port={port} attempt={attempt}: {error}"),
                    );
                }
            }
        }

        if attempt >= 25 && !port_available(port) {
            append_launcher_log(
                &root,
                format!("backend tcp fallback succeeded port={port} attempt={attempt}"),
            );
            *state.child.lock().expect("child lock") = child.take();
            *state.port.lock().expect("port lock") = Some(port);
            *state.owned.lock().expect("owned lock") = true;
            return BackendLaunchResult {
                ok: true,
                port: Some(port),
                base_url: Some(api_base(port)),
                owned: true,
                message: format!("Started backend on port {port}"),
            };
        }
    }

    append_launcher_log(
        &root,
        format!(
            "backend startup timed out port={port} log={}",
            log_path.display()
        ),
    );
    if let Some(mut child) = child {
        let _ = stop_child(&mut child);
    }
    BackendLaunchResult {
        ok: false,
        port: Some(port),
        base_url: Some(api_base(port)),
        owned: false,
        message: format!("Backend startup timed out. Log: {}", log_path.display()),
    }
}

#[tauri::command]
fn stop_backend(state: tauri::State<BackendState>) -> Result<(), String> {
    let owned = *state.owned.lock().map_err(|error| error.to_string())?;
    if !owned {
        return Ok(());
    }
    if let Some(mut child) = state
        .child
        .lock()
        .map_err(|error| error.to_string())?
        .take()
    {
        stop_child(&mut child)?;
    }
    *state.port.lock().map_err(|error| error.to_string())? = None;
    *state.owned.lock().map_err(|error| error.to_string())? = false;
    Ok(())
}

#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let url = url.trim();
    let lower_url = url.to_ascii_lowercase();
    if !(lower_url.starts_with("http://") || lower_url.starts_with("https://")) {
        return Err("Only http and https URLs can be opened".to_string());
    }

    #[cfg(windows)]
    {
        let mut command = Command::new("rundll32.exe");
        command.args(["url.dll,FileProtocolHandler", url]);
        command.creation_flags(0x08000000);
        return command
            .spawn()
            .map(|_| ())
            .map_err(|error| error.to_string());
    }

    #[cfg(target_os = "macos")]
    {
        return Command::new("open")
            .arg(url)
            .spawn()
            .map(|_| ())
            .map_err(|error| error.to_string());
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        return Command::new("xdg-open")
            .arg(url)
            .spawn()
            .map(|_| ())
            .map_err(|error| error.to_string());
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![
            ensure_backend,
            stop_backend,
            open_external_url
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
