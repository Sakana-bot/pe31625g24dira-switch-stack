#!/bin/bash
set -euo pipefail

REPOSITORY=${PE31625G24DIRA_REPOSITORY:-Sakana-bot/pe31625g24dira-switch-stack}
MODE=install
[ "${1:-}" != --upgrade ] || MODE=upgrade
[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

for command in curl python3 sha256sum tar; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done

work=$(mktemp -d "${TMPDIR:-/var/tmp}/pe31625g24dira-bootstrap.XXXXXX")
trap 'rm -rf -- "$work"' EXIT HUP INT TERM
headers=(-H 'Accept: application/vnd.github+json')
[ -z "${GITHUB_TOKEN:-}" ] || headers+=(-H "Authorization: Bearer $GITHUB_TOKEN")

release_json="$work/release.json"
curl --fail --location --silent --show-error "${headers[@]}" \
    "https://api.github.com/repos/$REPOSITORY/releases/latest" -o "$release_json"

runtime_release_json="$release_json"
if [ "$MODE" = install ]; then
    runtime_release_tag=${PE31625G24DIRA_RUNTIME_RELEASE_TAG:-v1.0.0}
    runtime_release_json="$work/runtime-release.json"
    curl --fail --location --silent --show-error "${headers[@]}" \
        "https://api.github.com/repos/$REPOSITORY/releases/tags/$runtime_release_tag" \
        -o "$runtime_release_json"
fi

readarray -t asset_data < <(python3 - "$release_json" "$MODE" "$runtime_release_json" <<'PY'
import json, re, sys
release = json.load(open(sys.argv[1], encoding="utf-8"))
mode = sys.argv[2]
runtime_release = json.load(open(sys.argv[3], encoding="utf-8"))
assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
runtime_assets = {
    a["name"]: a["browser_download_url"] for a in runtime_release.get("assets", [])
}
def emit(source, pattern):
    matches = [(name, url) for name, url in source.items() if re.fullmatch(pattern, name)]
    if len(matches) != 1:
        raise SystemExit(f"expected one matching release asset, found {len(matches)}")
    name, url = matches[0]
    sidecar = name + ".sha256"
    if sidecar not in source:
        raise SystemExit("release checksum asset is missing")
    print(name)
    print(url)
    print(source[sidecar])

emit(assets, r"pe31625g24dira-deploy-kit-.*\.tar\.gz$")
if mode == "install":
    emit(runtime_assets, r"pe31625g24dira-legacy-sdk-runtime-.*\.tar\.gz$")
PY
)

archive=${asset_data[0]}
curl --fail --location --silent --show-error "${headers[@]}" "${asset_data[1]}" -o "$work/$archive"
curl --fail --location --silent --show-error "${headers[@]}" "${asset_data[2]}" -o "$work/$archive.sha256"
(cd "$work" && sha256sum -c "$archive.sha256")
tar -C "$work" -xzf "$work/$archive"
kit=$(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'pe31625g24dira-*kit-*' -print -quit)
[ -n "$kit" ] || { echo "Invalid release archive." >&2; exit 1; }

if [ "$MODE" = upgrade ]; then
    bash "$kit/deployment/upgrade-debian13.sh" --audit
    bash "$kit/deployment/upgrade-debian13.sh" --apply
else
    runtime=${asset_data[3]}
    curl --fail --location --silent --show-error "${headers[@]}" "${asset_data[4]}" -o "$work/$runtime"
    curl --fail --location --silent --show-error "${headers[@]}" "${asset_data[5]}" -o "$work/$runtime.sha256"
    (cd "$work" && sha256sum -c "$runtime.sha256")
    echo "Building the fm10k UIO driver locally for kernel $(uname -r)."
    bash "$kit/deployment/deploy-debian13.sh" --runtime "$work/$runtime" --audit
    bash "$kit/deployment/deploy-debian13.sh" --runtime "$work/$runtime"
    echo "Installation complete. Reboot the board when ready."
fi
