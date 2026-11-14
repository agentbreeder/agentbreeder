#!/bin/sh
# Runs on every `services up` after health (idempotent). One login role per database, named after
# it: the role owns its database, PUBLIC loses CONNECT, so a role can't reach another database.
# Extra `roles` get least privilege in `roles_database`, and `console_database` gets the console
# role (see below). The superuser stays inside this step. Role passwords are generated hex tokens.
set -eu
export PGHOST="$AGENTBREEDER_SERVICE" PGPORT=5432 PGUSER=postgres PGDATABASE=postgres
PGPASSWORD="$(cat /run/secrets/superuser)"
export PGPASSWORD

# Reserved: the console role (below). No `databases` or `roles` item may use it.
CONSOLE_ROLE=agentbreeder_console

check_name() {
  case "$1" in
    "" | *[!a-z0-9_]* | [!a-z]* | pg_* | postgres | "$CONSOLE_ROLE")
      echo "invalid $2 name" >&2; exit 3 ;;
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

# A database named like the console role fails closed (it can't come from `databases`).
# Assigned first, so `set -e` stops on a psql error instead of passing an empty result.
reserved_db="$(psql -v ON_ERROR_STOP=1 -qAt -v name="$CONSOLE_ROLE" <<SQL
SELECT 1 FROM pg_database WHERE datname = :'name'
SQL
)"
if [ -n "$reserved_db" ]; then
  echo "a database named $CONSOLE_ROLE exists; it is reserved" >&2; exit 3
fi

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

# The console role (`console_database`): AgentBreeder's own CLI commands read and write the
# tables the database owner made with it, after that owner's init_sql grants them (so it is made
# before init_sql runs). It can connect to that one database and holds nothing else: no schema,
# no membership in or of any role, no CREATE or TEMP (PUBLIC has no rights on declared
# databases). A role of that name that owns anything or has a membership was not made here:
# fail closed. Every run revokes its own CONNECT on every other database (a database made
# outside the stack that still lets PUBLIC connect stays reachable, as for every role); unset,
# the role can't log in and its sessions end, so a removed console credential stops working.
console_exists="$(psql -v ON_ERROR_STOP=1 -qAt -v name="$CONSOLE_ROLE" <<SQL
SELECT 1 FROM pg_roles WHERE rolname = :'name'
SQL
)"
if [ -n "$console_exists" ]; then
  foreign="$(psql -v ON_ERROR_STOP=1 -qAt -v name="$CONSOLE_ROLE" <<SQL
SELECT 1 FROM pg_roles r WHERE r.rolname = :'name' AND (
  EXISTS (SELECT 1 FROM pg_shdepend d WHERE d.refclassid = 'pg_authid'::regclass
          AND d.refobjid = r.oid AND d.deptype = 'o')
  OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid OR m.roleid = r.oid))
SQL
)"
  [ -z "$foreign" ] || { echo "role $CONSOLE_ROLE owns objects or has memberships" >&2; exit 3; }
fi
console_db="${CONSOLE_DATABASE:-}"
if [ -n "$console_db" ]; then
  check_name "$console_db" console_database
  if [ ! -f "/run/secrets/role/$console_db" ]; then
    echo "console_database must be a declared database" >&2; exit 3
  fi
  password="$(cat /run/secrets/console)"
  check_password "$password"
  login_role "$CONSOLE_ROLE" "$password"
  psql -v ON_ERROR_STOP=1 -qAt -v role="$CONSOLE_ROLE" -v db="$console_db" <<SQL
SELECT format('ALTER ROLE %I CONNECTION LIMIT 10 VALID UNTIL %L', :'role', 'infinity') \\gexec
SELECT format('ALTER ROLE %I RESET ALL', :'role') \\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', datname, :'role') FROM pg_database
  WHERE datname <> :'db' \\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', :'db', :'role') \\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'role') \\gexec
SQL
elif [ -n "$console_exists" ]; then
  psql -v ON_ERROR_STOP=1 -qAt -v role="$CONSOLE_ROLE" >/dev/null <<SQL
SELECT format('ALTER ROLE %I NOLOGIN CONNECTION LIMIT 0', :'role') \\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', datname, :'role') FROM pg_database \\gexec
SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = :'role';
SQL
fi

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
