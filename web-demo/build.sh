#!/usr/bin/env bash
# 체험판 한 파일을 만든다.
#
# 시나리오(scenarios/festival_01.json)를 그대로 박아 넣으므로, 시나리오를 고쳤으면
# 이 스크립트를 다시 돌려야 체험판도 같이 바뀐다.
#
#   ./web-demo/build.sh
#   -> web-demo/index.html
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=web-demo/index.html
SRC=web-demo/src

python3 - <<'PY'
import json, pathlib
raw = json.loads(pathlib.Path("scenarios/festival_01.json").read_text(encoding="utf-8"))
pathlib.Path("web-demo/src/scenario.js").write_text(
    "const SCENARIO=" + json.dumps(raw, ensure_ascii=False, separators=(",", ":")) + ";\n",
    encoding="utf-8")
PY

{
  cat "$SRC/shell.html"
  printf '<script>\n'
  cat "$SRC/scenario.js" "$SRC/engine.js" "$SRC/ui.js" "$SRC/result.js" "$SRC/app.js"
  printf '</script>\n'
} > "$OUT"

echo "만들었다: $OUT ($(wc -c < "$OUT") bytes)"
