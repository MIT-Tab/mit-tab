use std::{
    env, fs,
    io::ErrorKind,
    os::unix::fs::{DirBuilderExt, OpenOptionsExt},
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use chrono::Utc;
use tokio::{process::Command, time::timeout};

use crate::passwords;

pub struct Backups {
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
    pub ca: Option<PathBuf>,
    pub prefix: PathBuf,
    pub s3: bool,
}

#[derive(Clone, Copy)]
pub enum Trigger {
    Pair,
    RePair,
    Rooms,
    Judges,
}

const BACKUP_ERROR: &str = "Could not save the database backup. The pairings are unchanged.";

impl Backups {
    pub async fn pre_pairing(&self, round: i32, trigger: Trigger) -> Result<(), &'static str> {
        if self.s3 {
            return Err("This instance requires S3 backups before changing pairings.");
        }
        let (name, kind) = match trigger {
            Trigger::Pair => (format!("Before Pairing Round {round}"), 4),
            Trigger::RePair => (format!("Before Re-pairing Round {round}"), 8),
            Trigger::Rooms => (format!("Before Room Assign Round {round}"), 5),
            Trigger::Judges => (format!("Before Judge Assign Round {round}"), 3),
        };
        let seconds = Utc::now().timestamp();
        let temporary = self
            .prefix
            .join(format!(".backup-{}", passwords::random_secret()?));
        let temporary = self.snapshot(&temporary).await.map_err(|_| BACKUP_ERROR)?;
        for counter in 1.. {
            let label = if counter == 1 {
                name.clone()
            } else {
                format!("{name} ({counter})")
            };
            let destination = self
                .prefix
                .join(format!("{label}_{kind}_{round}_{seconds}_1.dump.sql"));
            match fs::hard_link(&temporary.0, destination) {
                Ok(()) => return Ok(()),
                Err(err) if err.kind() == ErrorKind::AlreadyExists => {}
                Err(_) => return Err(BACKUP_ERROR),
            }
        }
        unreachable!("unbounded backup filename sequence")
    }

    async fn snapshot(&self, path: &Path) -> Result<TemporaryFile, ()> {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&self.prefix)
            .map_err(|_| ())?;
        let program = env::var_os("MITTAB_MYSQLDUMP").unwrap_or_else(|| "mysqldump".into());
        let help = timeout(
            Duration::from_secs(10),
            Command::new(&program)
                .arg("--help")
                .stderr(Stdio::null())
                .kill_on_drop(true)
                .output(),
        )
        .await
        .map_err(|_| ())?
        .map_err(|_| ())?;
        if !help.status.success() {
            return Err(());
        }

        let file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)
            .map_err(|_| ())?;
        let partial = TemporaryFile(path.to_owned());
        let mut command = Command::new(program);
        command
            .args([
                "--no-defaults",
                "--protocol=TCP",
                "--quick",
                "--complete-insert",
                "--hex-blob",
                "--single-transaction",
                "--skip-lock-tables",
                "--skip-add-locks",
                "--no-tablespaces",
            ])
            .arg(format!("--host={}", self.host))
            .arg(format!("--port={}", self.port))
            .arg(format!("--user={}", self.user))
            .env("MYSQL_PWD", &self.password)
            .stdin(Stdio::null())
            .stdout(Stdio::from(file.try_clone().map_err(|_| ())?))
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let help = String::from_utf8_lossy(&help.stdout);
        if help.contains("set-gtid-purged") {
            command.arg("--set-gtid-purged=OFF");
        }
        if let Some(ca) = &self.ca {
            if help.contains("--ssl-mode") {
                command.arg("--ssl-mode=VERIFY_IDENTITY");
            } else {
                command.args(["--ssl", "--ssl-verify-server-cert"]);
            }
            command.arg("--ssl-ca").arg(ca);
        }
        command.arg("--").arg(&self.database);
        let status = timeout(Duration::from_secs(90), command.status())
            .await
            .map_err(|_| ())?
            .map_err(|_| ())?;
        if !status.success() {
            return Err(());
        }
        file.sync_all().map_err(|_| ())?;
        Ok(partial)
    }
}

struct TemporaryFile(PathBuf);

impl Drop for TemporaryFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
