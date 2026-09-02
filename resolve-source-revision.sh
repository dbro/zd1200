#!/usr/bin/env bash
# Resolve the source revision without granting the runtime access to Docker or
# copying the working tree into the vendor-derived runtime volume.
set -euo pipefail

git_dir="${1:-/source-git}"
override="${ZD_VIRTUAL_BUILD_ID:-}"

fail() {
    echo "zd1200-prepare: $*" >&2
    exit 2
}

validate_commit() {
    case "$1" in
        ''|*[!0-9A-Fa-f]*) return 1 ;;
    esac
    [ "${#1}" -ge 7 ] && [ "${#1}" -le 64 ]
}

if [ -n "$override" ]; then
    validate_commit "$override" \
        || fail "ZD_VIRTUAL_BUILD_ID must be a 7-64 character hexadecimal revision"
    printf '%.7s\n' "${override,,}"
    exit 0
fi

if [ ! -r "$git_dir/HEAD" ]; then
    # Portainer's checkout is not necessarily visible to the Docker daemon.
    # Keep deployment working; callers can provide ZD_VIRTUAL_BUILD_ID when
    # they need an identifying revision in the admin UI.
    printf '0000000\n'
    exit 0
fi
head_value="$(tr -d '\r\n' < "$git_dir/HEAD")"
case "$head_value" in
    'ref: refs/'*)
        ref=${head_value#ref: }
        case "$ref" in
            *..*|*[!A-Za-z0-9_./-]*) fail "invalid Git HEAD reference" ;;
        esac
        if [ -r "$git_dir/$ref" ]; then
            commit="$(tr -d '\r\n' < "$git_dir/$ref")"
        elif [ -r "$git_dir/packed-refs" ]; then
            commit="$(awk -v wanted="$ref" '$1 !~ /^#/ && $2 == wanted { print $1; exit }' "$git_dir/packed-refs")"
        else
            commit=""
        fi
        ;;
    *) commit=$head_value ;;
esac

validate_commit "$commit" || fail "Git HEAD does not resolve to a hexadecimal commit"
printf '%.7s\n' "${commit,,}"
