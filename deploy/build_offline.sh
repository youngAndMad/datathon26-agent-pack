#!/usr/bin/env bash
# Build a portable offline bundle under dist/offline-bundle/ (spec §12, §14 `make build-offline`).
#
# Contents: source tree (tracked + unignored files only), hashed requirements lock exported from
# uv.lock, optional wheelhouse, frontend/dist, rules + legal snapshots, model checksums (and
# optionally the model files), build info, BUNDLE_README.txt and MANIFEST.sha256.
# Never included: data/, secrets/, *.mov/*.mp4, .env/cashwatch.env, evaluation private dirs, dist/.
#
# Default mode performs NO network access. Options:
#   --with-models    copy the selected model directory into the bundle (APFS clone when possible)
#   --fetch-wheels   EXPLICIT ONLINE PREPARATION: download pinned wheels into bundle/wheelhouse
#                    with `python3 -m pip download --require-hashes` (never during judging)
# Env: CASHWATCH_MODEL_PATH (default models/qwen3-vl-4b-instruct-mlx-8bit), BUNDLE_EXTRAS (default mlx)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/dist/offline-bundle"
WITH_MODELS=0; FETCH_WHEELS=0
for a in "$@"; do
  case "$a" in
    --with-models) WITH_MODELS=1 ;;
    --fetch-wheels) FETCH_WHEELS=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
MODEL_PATH="${CASHWATCH_MODEL_PATH:-$ROOT/models/qwen3-vl-4b-instruct-mlx-8bit}"
EXTRAS="${BUNDLE_EXTRAS:-mlx}"
status=0
note() { echo "[build-offline] $*"; }
warn() { echo "[build-offline] WARN: $*" >&2; }

command -v uv >/dev/null || { echo "uv is required (pinned toolchain)" >&2; exit 2; }
case "$OUT" in "$ROOT"/dist/*) ;; *) echo "unsafe output path $OUT" >&2; exit 2 ;; esac
rm -rf "$OUT"
mkdir -p "$OUT"

# 1. Source tree: only files git would commit (respects .gitignore), minus restricted patterns.
note "copying source tree"
excluded_re='(^|/)(data|secrets|dist|build|node_modules|\.venv|__pycache__)/|\.(mov|MOV|mp4|MP4|m4v|avi|mkv)$|(^|/)\.env$|(^|/)deploy/cashwatch\.env$|^evaluation/(annotations|manifest|reports)/private/|^models/(?!manifest\.json$|README\.md$|manifest\.proposed\.json$)|\.(safetensors|gguf|pt|pth|ckpt|npz)$'
git ls-files -co --exclude-standard -z \
  | python3 -c '
import re, sys
pat = re.compile(sys.argv[1])
items = [p for p in sys.stdin.buffer.read().split(b"\0") if p]
keep = [p for p in items if not pat.search(p.decode("utf-8", "surrogateescape"))]
sys.stdout.buffer.write(b"\0".join(keep) + (b"\0" if keep else b""))
' "$excluded_re" \
  | tar --null -T - -cf - | tar -xf - -C "$OUT" --no-same-owner

# 2. Requirements lock from uv.lock (hashes kept; no network: --offline --frozen).
note "exporting requirements lock (extras: $EXTRAS)"
extra_args=()
IFS=',' read -ra _ex <<< "$EXTRAS"
for e in "${_ex[@]}"; do [[ -n "$e" ]] && extra_args+=(--extra "$e"); done
if ! uv export --frozen --offline --no-dev --no-emit-project --format requirements-txt \
      "${extra_args[@]}" -o "$OUT/requirements.lock.txt" >/dev/null 2>"$OUT/.uv-export.err"; then
  warn "uv export failed: $(head -c 300 "$OUT/.uv-export.err")"; status=1
fi
rm -f "$OUT/.uv-export.err"
uv export --frozen --offline --only-dev --no-emit-project --format requirements-txt \
  -o "$OUT/requirements-dev.lock.txt" >/dev/null 2>&1 || warn "dev lock export failed"

# 3. Optional wheelhouse (explicit online preparation only).
if [[ $FETCH_WHEELS -eq 1 ]]; then
  note "ONLINE PREPARATION: downloading pinned wheels (macOS arm64, CPython 3.12)"
  osmaj="$(sw_vers -productVersion | cut -d. -f1)"
  mkdir -p "$OUT/wheelhouse"
  if HF_HUB_OFFLINE=0 python3 -m pip download --disable-pip-version-check --require-hashes --no-deps \
       --only-binary=:all: --platform "macosx_${osmaj}_0_arm64" --python-version 3.12 \
       --implementation cp --abi cp312 --abi abi3 --abi none \
       -r "$OUT/requirements.lock.txt" -d "$OUT/wheelhouse" >"$OUT/wheelhouse/.download.log" 2>&1; then
    note "wheels: $(ls "$OUT/wheelhouse"/*.whl 2>/dev/null | wc -l | tr -d ' ')"
  else
    warn "wheel download incomplete (see wheelhouse/.download.log)"; status=1
  fi
  python3 -m pip download --disable-pip-version-check --only-binary=:all: \
    -d "$OUT/wheelhouse" "hatchling==1.27.0" >>"$OUT/wheelhouse/.download.log" 2>&1 \
    || warn "hatchling build backend wheel not downloaded (needed to install the project offline)"
else
  note "wheelhouse not included (use --fetch-wheels during online preparation)"
fi

# 4. Frontend production build (git-ignored, copied explicitly).
if [[ -f "$ROOT/frontend/dist/index.html" ]]; then
  note "copying frontend/dist"
  mkdir -p "$OUT/frontend"
  cp -R "$ROOT/frontend/dist" "$OUT/frontend/dist"
  if grep -RIlE '<(script|link)[^>]+(src|href)="https?://' "$OUT/frontend/dist" >/dev/null 2>&1; then
    warn "frontend/dist references external script/style URLs (must be bundled locally)"; status=1
  fi
else
  warn "frontend/dist missing — run 'make frontend' before building the bundle"; status=1
fi

# 5. Model checksums (and optional copy). .cache/ download metadata is excluded.
if [[ -d "$MODEL_PATH" ]]; then
  mname="$(basename "$MODEL_PATH")"
  note "hashing model files in models/$mname (this reads several GB)"
  ( cd "$(dirname "$MODEL_PATH")" && find "$mname" -type f ! -path '*/.cache/*' -print0 | sort -z \
      | xargs -0 shasum -a 256 ) > "$OUT/models.sha256"
  if [[ -f "$ROOT/models/manifest.json" ]]; then
    python3 - "$ROOT/models/manifest.json" "$OUT/models.sha256" <<'PY' || status=1
import json, sys
man = json.load(open(sys.argv[1]))
have = {}
for line in open(sys.argv[2]):
    h, p = line.rstrip("\n").split("  ", 1)
    have[p.split("/", 1)[1] if "/" in p else p] = h
recorded = {}
def walk(o):
    if isinstance(o, dict):
        name = o.get("path") or o.get("file") or o.get("name")
        if isinstance(o.get("sha256"), str) and isinstance(name, str):
            recorded[name.split("/")[-1] if "/" in name else name] = o["sha256"]
        for v in o.values(): walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
walk(man)
by_base = {k.split("/")[-1]: v for k, v in have.items()}
bad = [n for n, h in recorded.items() if n in by_base and by_base[n] != h]
checked = [n for n in recorded if n in by_base]
print(f"[build-offline] manifest hashes checked={len(checked)} mismatched={len(bad)}")
if bad:
    print("[build-offline] WARN: hash mismatch for " + ", ".join(sorted(bad)), file=sys.stderr)
    sys.exit(1)
PY
  else
    warn "models/manifest.json missing (orchestrator-owned) — weights provenance/license not recorded"; status=1
  fi
  if [[ $WITH_MODELS -eq 1 ]]; then
    note "copying model directory (clone-on-write where supported)"
    mkdir -p "$OUT/models"
    cp -c -R "$MODEL_PATH" "$OUT/models/" 2>/dev/null || cp -R "$MODEL_PATH" "$OUT/models/"
    rm -rf "$OUT/models/$mname/.cache"
  fi
else
  warn "model directory $MODEL_PATH missing — run 'make fetch-models' during online preparation"; status=1
fi

# 6. Build info (versions only; no secrets, no host identifiers beyond OS/arch).
{
  echo "built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git_head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "git_dirty=$([[ -n "$(git status --porcelain 2>/dev/null)" ]] && echo yes || echo no)"
  echo "os=$(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null) $(uname -m)"
  echo "uv=$(uv --version 2>/dev/null)"
  echo "python=$("$ROOT/.venv/bin/python" -V 2>&1 || echo missing)"
  echo "ffmpeg=$(ffmpeg -version 2>/dev/null | head -1 || echo missing)"
  echo "extras=$EXTRAS with_models=$WITH_MODELS wheelhouse=$FETCH_WHEELS"
} > "$OUT/BUILD_INFO.txt"

cat > "$OUT/BUNDLE_README.txt" <<'TXT'
cashwatch offline bundle — install without internet (see docs/runbook/RUNBOOK.md, "Offline install")

1. Prerequisites on the target (Apple Silicon macOS): uv, CPython 3.12 (uv-managed or system),
   ffmpeg/ffprobe on PATH. Copy this directory to the target.
2. Python environment, one of:
   a) wheelhouse/ present:  uv venv --python 3.12 .venv &&
      uv pip install --offline --no-index --find-links wheelhouse --require-hashes -r requirements.lock.txt &&
      uv pip install --offline --no-index --find-links wheelhouse --no-deps .
   b) same machine with a pre-populated uv cache:  uv sync --frozen --offline --extra mlx
3. Models: if models/ is absent, copy the model directory to models/ and verify:
   shasum -a 256 -c models.sha256   (run inside models/)
4. Verify bundle integrity:  shasum -a 256 -c MANIFEST.sha256
5. Generate the jury key (bash deploy/gen_api_key.sh), then make doctor && make start.
TXT

# 7. Safety scan of the bundle itself.
leaks="$(find "$OUT" \( -iname '*.mov' -o -iname '*.mp4' -o -name 'jury_api_key' -o -name '.env' \
          -o -name 'cashwatch.env' -o -path '*/secrets/*' -o -path '*/data/jobs/*' \) -print | head -5)"
if [[ -n "$leaks" ]]; then
  echo "[build-offline] FAIL: restricted files in bundle:" >&2; echo "$leaks" >&2
  rm -rf "$OUT"; exit 1
fi

# 8. Integrity manifest.
note "writing MANIFEST.sha256"
( cd "$OUT" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 shasum -a 256 ) > "$OUT/MANIFEST.sha256.tmp"
mv "$OUT/MANIFEST.sha256.tmp" "$OUT/MANIFEST.sha256"
note "bundle at $OUT ($(du -sh "$OUT" | cut -f1)), files: $(wc -l < "$OUT/MANIFEST.sha256" | tr -d ' ')"
[[ $status -eq 0 ]] || warn "bundle built with warnings above (incomplete for release)"
exit $status
