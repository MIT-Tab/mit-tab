use std::{env, net::SocketAddr, path::PathBuf, time::Duration};

use sqlx::mysql::{MySqlConnectOptions, MySqlPoolOptions, MySqlSslMode};

pub struct Config {
    pub listen: SocketAddr,
    pub db: MySqlConnectOptions,
    pub django_secret: Option<String>,
}

impl Config {
    pub fn from_env() -> Result<Self, &'static str> {
        let listen = env::var("MITTAB_RUST_LISTEN")
            .unwrap_or_else(|_| "127.0.0.1:8000".into())
            .parse()
            .map_err(|_| "MITTAB_RUST_LISTEN must be a socket address")?;
        let host = env::var("MYSQL_HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = env::var("MYSQL_PORT")
            .unwrap_or_else(|_| "3306".into())
            .parse()
            .map_err(|_| "MYSQL_PORT must be a TCP port")?;
        let database = env::var("MYSQL_DATABASE").unwrap_or_else(|_| "mittab".into());
        let username = env::var("MYSQL_USER").unwrap_or_else(|_| "root".into());
        let password = env::var("MYSQL_PASSWORD").unwrap_or_default();
        let options = MySqlConnectOptions::new()
            .host(&host)
            .port(port)
            .database(&database)
            .username(&username)
            .password(&password)
            .charset("utf8mb4")
            .timezone(Some("+00:00".into()));

        let db = if matches!(host.as_str(), "localhost" | "127.0.0.1" | "::1") {
            options.ssl_mode(MySqlSslMode::Preferred)
        } else {
            let ca = env::var_os("MYSQL_SSL_CA")
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    PathBuf::from("tmp").join(
                        env::var_os("MYSQL_SSL_FILENAME")
                            .unwrap_or_else(|| "digitalocean-db-ca.pem".into()),
                    )
                });
            if !ca.is_file() {
                return Err("Configure a readable MYSQL_SSL_CA for remote MySQL");
            }
            options.ssl_mode(MySqlSslMode::VerifyIdentity).ssl_ca(ca)
        };
        let django_secret = env::var("MITTAB_DJANGO_SECRET_KEY")
            .ok()
            .filter(|key| !key.is_empty());
        Ok(Self {
            listen,
            db,
            django_secret,
        })
    }

    pub async fn connect(&self) -> Result<sqlx::MySqlPool, &'static str> {
        MySqlPoolOptions::new()
            .max_connections(10)
            .acquire_timeout(Duration::from_secs(5))
            .connect_with(self.db.clone())
            .await
            .map_err(|_| "Could not connect to the MySQL database")
    }
}
