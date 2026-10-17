"""PREPARATION-ONLY official legal source capture. Never imported by the service at runtime.

Usage (from the repository root, with network access, before going offline):

    .venv/bin/python -m cashwatch.rules.capture --out rules/sources

Retrieves Kazakhstan legal acts only from the official ИПС «Әділет» (adilet.zan.kz, operated by
РГП на ПХВ «Институт законодательства и правовой информации РК» МЮ РК):

* ``https://adilet.zan.kz/api/documents/by-ngr/<NGR>?language=rus`` — the JSON endpoint the
  official single-page application at ``https://adilet.zan.kz/rus/docs/<NGR>`` itself calls
  (the HTML page is a JavaScript shell without the act text);
* ``https://old.adilet.zan.kz/rus/docs/<NGR>[/info|/history]`` — the legacy official HTML
  endpoint, kept as corroboration and for edition/history metadata.

Writes raw response bodies byte-for-byte, a deterministic derived ``.txt`` for the act text,
``manifest.yaml`` (URL, UTC retrieval time, HTTP status, size, sha256) and ``excerpts.yaml``
(verbatim slices of the derived text with character offsets). Excerpt markers below are
*locators*; the stored excerpt text is always taken from the snapshot, never typed by hand.
Any failure is recorded in the manifest and the excerpt is omitted (rule stays disabled).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from cashwatch.rules.sources import html_to_text, sha256_bytes

UA = "Mozilla/5.0 (cashwatch legal-source capture; preparation only)"

# source_id -> official NGR code
DOCS: dict[str, str] = {
    "L01": "V1900019680",
    "L02": "V2500037429",
    "L03": "V2200027073",
    "L04": "V2600038217",
    "L05": "V2000020075",
}
# Extra legacy pages (metadata/history) captured for edition verification.
EXTRA_OLD = {
    "L01": ["", "/info", "/history"],
    "L02": ["", "/info"],
    "L04": ["/info"],
}

# (excerpt_id, source_id, unique line-start marker, number of lines, label)
EXCERPTS: list[tuple[str, str, str, int, str]] = [
    ("L01-p5", "L01", "5. Банк для осуществления кассовых операций оборудует кассовый узел – систему специально оборудованных помещений, предназначенных для проведения кассовых операций и операций по инкассации банкнот, монет и ценностей, устройство которого определяется Правилами организации охраны и устройства помещений банков второго уровня, филиалов", 1, "п. 5 (кассовый узел)"),
    ("L01-p2-10", "L01", "10) кассовый работник – работник банка", 1, "п. 2 пп. 10) (кассовый работник)"),
    ("L01-p12", "L01", "12. Хранение личных вещей", 2, "п. 12"),
    ("L01-p12-note", "L01", "Сноска. Пункт 12 – в редакции", 1, "сноска к п. 12"),
    ("L01-p19", "L01", "19. Кассовый работник банка осуществляет следующие действия:", 7, "п. 19"),
    ("L01-p19-note", "L01", "Сноска. Пункт 19 - в редакции", 1, "сноска к п. 19"),
    ("L01-p20", "L01", "20. Наличные деньги принимаются и пересчитываются", 2, "п. 20"),
    ("L01-p22", "L01", "22. Кассовому работнику не допускается", 1, "п. 22"),
    ("L01-p24", "L01", "24. Кассовые операции по приему наличных денег клиента от подразделения инкассации", 6, "п. 24"),
    ("L01-p25", "L01", "25. В случае обнаружения нарушения целостности сумки", 2, "п. 25"),
    ("L01-p31", "L01", "31. Кассовый работник банка осуществляет следующие действия:", 7, "п. 31"),
    ("L01-p32", "L01", "32. Кассовый работник выдает клиенту банкноты", 2, "п. 32"),
    ("L01-p42", "L01", "42. Кассовые операции по пересчету, сортировке и упаковке", 1, "п. 42"),
    ("L01-p107", "L01", "107. Прием банкнот, монет и ценностей от клиентов", 5, "п. 107"),
    ("L02-p4", "L02", "4. Настоящее постановление вводится в действие", 1, "п. 4 (введение в действие)"),
    ("L02-amend-p12", "L02", "пункт 12 изложить в следующей редакции:", 3, "изменение п. 12 Правил (НБРК № 231)"),
    ("L02-amend-p19", "L02", "подпункты 2) и 3) пункта 19 изложить в следующей редакции:", 3, "изменение пп. 2), 3) п. 19 Правил"),
    ("L03-p5", "L03", "5. Настоящее постановление вводится в действие", 1, "п. 5 (введение в действие)"),
    ("L03-amend-p31", "L03", "пункт 31 изложить в следующей редакции:", 8, "изменение п. 31 Правил"),
    ("L04-p6", "L04", "6. Настоящее постановление вводится в действие", 1, "п. 6 (введение в действие)"),
]

METADATA_FIELDS = (
    "official_name", "title_ru", "status", "is_actual", "adoption_number", "adoption_date",
    "justice_reg_number", "justice_reg_date", "publication_reference", "publication_date",
    "start_date", "end_date", "updated_at", "group_number", "developer_organ_names_ru",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch(url: str, timeout: float = 90.0) -> tuple[int | None, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs)
            return resp.status, resp.read(), ""
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", f"HTTPError {e.code}"
    except Exception as e:  # timeout, DNS, TLS: recorded as a blocker
        return None, b"", f"{type(e).__name__}: {e}"


def _locate(text: str, marker: str, n_lines: int) -> tuple[int, int]:
    lines = text.split("\n")
    pos, hits = 0, []
    starts = []
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(marker):
            hits.append(i)
    if len(hits) != 1:
        raise LookupError(f"marker matched {len(hits)} lines")
    i = hits[0]
    if i + n_lines > len(lines):
        raise LookupError("excerpt runs past end of text")
    start = starts[i] + (len(lines[i]) - len(lines[i].lstrip()))
    last = i + n_lines - 1
    end = starts[last] + len(lines[last].rstrip())
    return start, end


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("rules/sources"))
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--excerpts-only", action="store_true",
                    help="rebuild excerpts.yaml from already captured <ID>-<date>.txt files (no network)")
    args = ap.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    day = args.date
    manifest: dict = {"captured_by": "cashwatch.rules.capture", "retrieval_date": day,
                      "authority_note": "adilet.zan.kz (incl. legacy old.adilet.zan.kz) only; "
                                        "retrieval date is not an effective date", "files": [],
                      "documents": {}}
    texts: dict[str, tuple[str, str]] = {}  # source_id -> (txt file name, text)

    for sid, ngr in DOCS.items():
        if args.excerpts_only:
            tname = f"{sid}-{day}.txt"
            if (out / tname).exists():
                texts[sid] = (tname, (out / tname).read_bytes().decode("utf-8"))
            continue
        url = f"https://adilet.zan.kz/api/documents/by-ngr/{ngr}?language=rus"
        at = _now()
        status, body, err = fetch(url)
        rec = {"source_id": sid, "url": url, "canonical_url": f"https://adilet.zan.kz/rus/docs/{ngr}",
               "retrieved_at": at, "http_status": status, "error": err or None}
        if status == 200 and body:
            name = f"{sid}-{day}.api.json"
            (out / name).write_bytes(body)
            rec.update(file=name, bytes=len(body), sha256=sha256_bytes(body), kind="official_api_json")
            try:
                doc = json.loads(body.decode("utf-8"))["document"]
                text = html_to_text(doc["text_content"])
                tname = f"{sid}-{day}.txt"
                tb = text.encode("utf-8")
                (out / tname).write_bytes(tb)
                texts[sid] = (tname, text)
                manifest["files"].append({"source_id": sid, "file": tname, "derived_from": name,
                                          "derivation": "cashwatch.rules.sources.html_to_text(document.text_content)",
                                          "bytes": len(tb), "sha256": sha256_bytes(tb),
                                          "kind": "derived_text"})
                manifest["documents"][sid] = {k: doc.get(k) for k in METADATA_FIELDS}
                manifest["documents"][sid]["has_operative_text"] = bool(doc.get("has_content")) and len(text) > 1000
            except Exception as e:  # malformed: keep raw, record blocker
                rec["error"] = f"parse: {type(e).__name__}"
        manifest["files"].append(rec)

        for suffix in EXTRA_OLD.get(sid, []):
            url = f"https://old.adilet.zan.kz/rus/docs/{ngr}{suffix}"
            at = _now()
            status, body, err = fetch(url)
            rec = {"source_id": sid, "url": url, "retrieved_at": at, "http_status": status, "error": err or None}
            if status == 200 and body:
                name = f"{sid}-{day}.old{suffix.replace('/', '-')}.html"
                (out / name).write_bytes(body)
                rec.update(file=name, bytes=len(body), sha256=sha256_bytes(body), kind="official_legacy_html")
            manifest["files"].append(rec)

    excerpts = []
    failures = []
    for eid, sid, marker, n, label in EXCERPTS:
        if sid not in texts:
            failures.append({"excerpt_id": eid, "reason": "source text not captured"})
            continue
        tname, text = texts[sid]
        try:
            s, e = _locate(text, marker, n)
        except LookupError as ex:
            failures.append({"excerpt_id": eid, "reason": str(ex)})
            continue
        excerpts.append({"excerpt_id": eid, "source_id": sid, "label": label, "text_file": tname,
                         "text_sha256": sha256_bytes(text.encode("utf-8")), "start": s, "end": e,
                         "text": text[s:e]})
    if not args.excerpts_only:
        (out / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000),
                                           encoding="utf-8")
    (out / "excerpts.yaml").write_text(
        yaml.safe_dump({"generated_by": "cashwatch.rules.capture", "offsets": "python str indices into text_file",
                        "excerpts": excerpts, "failures": failures},
                       allow_unicode=True, sort_keys=False, width=100000), encoding="utf-8")
    print(f"captured {len(texts)} texts, {len(excerpts)} excerpts, {len(failures)} failures", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
