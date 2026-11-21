# Shared environment for mittab-task scripts. Source it; don't execute it.
# Gives every worktree its own dev DB (mittab_<slug>), test DB (test_mittab_<slug>),
# port and state dir, so parallel agents never collide.

MT_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "mittab-task: run this inside a mit-tab worktree" >&2
  exit 2
}
MT_ID="$(printf %s "$MT_ROOT" | cksum | cut -d' ' -f1)"
MT_SLUG="$(basename "$MT_ROOT")"
MT_SLUG="${MT_SLUG#mit-tab}"
MT_SLUG="${MT_SLUG#-}"
MT_SLUG="${MT_SLUG:-main}"

set -a
# shellcheck disable=SC1091
if [ -f "$MT_ROOT/.env" ]; then
  . "$MT_ROOT/.env"
fi
set +a

export MYSQL_DATABASE="mittab_$(printf %s "$MT_SLUG" | tr -c 'a-zA-Z0-9' '_' | cut -c1-30)_${MT_ID}"
export MT_PORT="${MT_PORT:-$((8100 + $(printf %s "$MT_ROOT" | cksum | cut -d' ' -f1) % 800))}"
export MT_STATE="${TMPDIR:-/tmp}/mittab-agent/${MT_SLUG}-${MT_ID}"
export MT_CHROME_LIBS="${MT_CHROME_LIBS:-$HOME/.cache/mittab-chrome-libs/usr/lib/x86_64-linux-gnu}"
mkdir -p "$MT_STATE"
export MITTAB_FILE_CACHE_LOCATION="$MT_STATE/cache"

mt_mysql() {
  MYSQL_PWD="$MYSQL_PASSWORD" mysql -h "$MYSQL_HOST" -u "$MYSQL_USER" -NBe "$1"
}

# gh is sometimes unauthenticated inside the Codex sandbox; fall back to the git credential.
mt_gh_auth() {
  if [ -z "${GH_TOKEN:-}" ] && ! gh auth status >/dev/null 2>&1; then
    GH_TOKEN="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null |
      sed -n 's/^password=//p')"
    export GH_TOKEN
  fi
}
