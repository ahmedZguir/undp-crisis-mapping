#!/bin/sh
set -eu

supabase migration up --workdir /work --db-url "$DB_URL"

# The role migration ships a dev password; replace it with the one from .env.
psql "$DB_URL" -v ON_ERROR_STOP=1 -v pw="$API_SERVICE_DB_PASSWORD" -q <<'SQL'
alter role api_service with password :'pw';
SQL
