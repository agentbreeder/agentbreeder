#!/bin/sh
# Runs on every `services up` after health (idempotent). One login role per database, named after
# it: the role owns its database, PUBLIC loses CONNECT, so a role can't reach another database.
# Extra `roles` get least privilege in `roles_database` (see below). The superuser stays inside
# this step. Role passwords are generated hex tokens.
set -eu
export PGHOST="$AGENTBREEDER_SERVICE" PGPORT=5432 PGUSER=postgres PGDATABASE=postgres
PGPASSWORD="$(cat /run/secrets/superuser)"
export PGPASSWORD

check_name() {
  case "$1" in
    "" | *[!a-z0-9_]* | [!a-z]* | pg_* | postgres) echo "invalid $2 name" >&2; exit 3 ;;
  esac
}

check_password() {
  case "$1" in
    "" | *[!0-9a-f]*) echo "invalid generated password" >&2; exit 3 ;;
  esac
}

# Role attributes for every login role this step manages. The password goes in on stdin
# (validated hex), never in argv where `ps` shows it.
login_role() {
  psql -v ON_ERROR_STOP=1 -qAt -v name="$1" <<SQL
\\set pw '$2'
SELECT format('CREATE ROLE %I LOGIN', :'name')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'name') \\gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
              'NOBYPASSRLS PASSWORD %L', :'name', :'pw') \\gexec
SQL
}

# The maintenance databases are not for applications.
psql -v ON_ERROR_STOP=1 -qAt -c 'REVOKE CONNECT ON DATABASE postgres FROM PUBLIC' \
  -c 'REVOKE CONNECT ON DATABASE template1 FROM PUBLIC'

for file in /run/secrets/role/*; do
  [ -e "$file" ] || continue
  db="${file#/run/secrets/role/}"
  check_name "$db" database
  password="$(cat "$file")"
  check_password "$password"
  login_role "$db" "$password"
  psql -v ON_ERROR_STOP=1 -qAt -v db="$db" <<SQL
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'db')
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'db') \\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \\gexec
SQL
  psql -v ON_ERROR_STOP=1 -qAt -d "$db" -c 'CREATE EXTENSION IF NOT EXISTS vector'
done

# Extra roles: each can connect only to roles_database, where it owns a schema named after it
# (first on its default search_path, so its unqualified CREATEs land there, never in `public`,
# which PostgreSQL 16 closes to CREATE). It gets no rights on the owner's tables and can't create
# schemas. The database's owner role becomes a member of it, so the owner's init_sql can grant
# to it and create objects for it.
for file in /run/secrets/login/*; do
  [ -e "$file" ] || continue
  role="${file#/run/secrets/login/}"
  check_name "$role" role
  db="${ROLES_DATABASE:-}"
  if [ -z "$db" ] || [ ! -f "/run/secrets/role/$db" ]; then
    echo "roles_database must be a declared database" >&2; exit 3
  fi
  if [ -e "/run/secrets/role/$role" ]; then
    echo "a role can't share a name with a database" >&2; exit 3
  fi
  password="$(cat "$file")"
  check_password "$password"
  login_role "$role" "$password"
  psql -v ON_ERROR_STOP=1 -qAt -v role="$role" -v db="$db" <<SQL
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'role') \\gexec
SELECT format('GRANT %I TO %I', :'role', :'db') \\gexec
SQL
  psql -v ON_ERROR_STOP=1 -qAt -d "$db" -v role="$role" <<SQL
SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I', :'role', :'role') \\gexec
SQL
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
