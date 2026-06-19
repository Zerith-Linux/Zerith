#!/usr/bin/env bash
# Shared helpers for the host-side CI scripts in scripts/ci/.
# Source this; do not execute it. See docs/ci-workflows.md.

# Log to stderr so stdout stays clean for any value a script means to emit.
log()  { printf '>> %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# require_env VAR... — fail unless every named variable is set and non-empty.
require_env() {
    local missing=0 var
    for var in "$@"; do
        if [ -z "${!var:-}" ]; then
            printf 'error: required env var %s is not set\n' "$var" >&2
            missing=1
        fi
    done
    [ "$missing" -eq 0 ] || exit 1
}
