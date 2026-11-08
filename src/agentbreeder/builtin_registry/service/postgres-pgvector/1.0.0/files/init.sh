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
  psql -v ON_ERROR_STOP=1 -qAt -v db="$db" -v pw="$password" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'db')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
              'NOBYPASSRLS PASSWORD %L', :'db', :'pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'db')
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SQL
  psql -v ON_ERROR_STOP=1 -qAt -d "$db" -c 'CREATE EXTENSION IF NOT EXISTS vector'
done

if [ -f /run/params/init_sql ]; then
  db="${INIT_SQL_DATABASE:-}"
  [ -n "$db" ] && [ -f "/run/secrets/role/$db" ] || { echo "init_sql_database must be a declared database" >&2; exit 3; }
  PGUSER="$db" PGDATABASE="$db" PGPASSWORD="$(cat "/run/secrets/role/$db")" \
    psql -v ON_ERROR_STOP=1 -qAt -f /run/params/init_sql
fi
