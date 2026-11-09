#!/bin/sh
# Runs on every `services up` after health (idempotent). One login role per database, named after
# it: the role owns its database, PUBLIC loses CONNECT, so a role can't reach another database.
# The superuser stays inside this step. Role passwords are generated hex tokens.
set -eu
export PGHOST="$AGENTBREEDER_SERVICE" PGPORT=5432 PGUSER=postgres PGDATABASE=postgres
PGPASSWORD="$(cat /run/secrets/superuser)"
export PGPASSWORD

for file in /run/secrets/role/*; do
  [ -e "$file" ] || continue
  db="${file#/run/secrets/role/}"
  case "$db" in
    "" | *[!a-z0-9_]* | [!a-z]*) echo "invalid database name" >&2; exit 3 ;;
  esac
  password="$(cat "$file")"
  case "$password" in
    "" | *[!0-9a-f]*) echo "invalid generated password" >&2; exit 3 ;;
  esac
  # The password goes in on stdin (validated hex above), never in argv where `ps` shows it.
  psql -v ON_ERROR_STOP=1 -qAt -v db="$db" <<SQL
\\set pw '$password'
SELECT format('CREATE ROLE %I LOGIN', :'db')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db') \\gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
              'NOBYPASSRLS PASSWORD %L', :'db', :'pw') \\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'db')
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'db') \\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \\gexec
SQL
  psql -v ON_ERROR_STOP=1 -qAt -d "$db" -c 'CREATE EXTENSION IF NOT EXISTS vector'
done

# init_sql comes from the repo. It is sent with `psql -c`, which hands the whole string to the
# server: psql runs no backslash meta-commands (`\!`, `\c`, `\copy`) unless the string starts
# with one, which is refused here. It runs as the database's own role (no superuser, no file or
# program access) in one implicit transaction. 64 KiB cap (one argv string is at most 128 KiB).
if [ -f /run/params/init_sql ]; then
  db="${INIT_SQL_DATABASE:-}"
  if [ -z "$db" ] || [ ! -f "/run/secrets/role/$db" ]; then
    echo "init_sql_database must be a declared database" >&2; exit 3
  fi
  [ "$(wc -c < /run/params/init_sql)" -le 65536 ] || { echo "init_sql is over 64 KiB" >&2; exit 3; }
  sql="$(cat /run/params/init_sql)"
  first="$(printf '%s' "$sql" | tr -d ' \t\r\n' | cut -c1)"
  [ "$first" != "\\" ] || { echo "init_sql must not start with a psql meta-command" >&2; exit 3; }
  PGUSER="$db" PGDATABASE="$db" PGPASSWORD="$(cat "/run/secrets/role/$db")" \
    psql -v ON_ERROR_STOP=1 -qAt -c "$sql"
fi
