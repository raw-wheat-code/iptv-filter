#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import argparse
from pathlib import Path
from typing import List, Dict
import requests

US_M3U_URL = "https://iptv-org.github.io/iptv/index.m3u"
OUTPUT_FILE = "channels.m3u"
ALLOWLIST_FILE = "allowlist.txt"     # one rule per line (examples below)
DENYLIST_FILE  = "denylist.txt"      # optional

# Toggle/behavior knobs
ENGLISH_ONLY = True
ALLOWED_LANGS = {"en", "eng", "english"}     # case-insensitive match
PREFER_HTTPS = True
PREFER_M3U8  = True
DEDUP_KEY    = "tvg-id"   # tvg-id | tvg-name | none
TIMEOUT      = 30

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "IPTV-Filter/1.0"})
    r.raise_for_status()
    return r.text

def tokenize_m3u(m3u_text: str) -> List[Dict]:
    """Return list of items: {attrs: {…}, display: str, url: str}"""
    items = []
    lines = [ln.rstrip("\n") for ln in m3u_text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            ext = lines[i]
            # Find the first non-comment line after EXTINF as the URL
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            url = lines[j].strip() if j < len(lines) else ""

            m = re.match(r'^#EXTINF:[^ ]*\s*(?P<attrs>.*?),(?P<name>.*)$', ext)
            attrs_text = m.group("attrs") if m else ""
            display = (m.group("name").strip() if m else "").strip()

            attrs = {}
            for key, val in re.findall(r'([A-Za-z0-9\-]+)="([^"]*)"', attrs_text):
                attrs[key.lower()] = val

            items.append({"attrs": attrs, "display": display, "url": url})
            i = j + 1
        else:
            i += 1
    return items

def norm(s: str) -> str:
    return (s or "").strip().lower()

def lang_ok(item: Dict) -> bool:
    if not ENGLISH_ONLY:
        return True
    val = item["attrs"].get("tvg-language", "")
    parts = [norm(p) for p in re.split(r"[;,/|]", val) if p.strip()]
    return any(p in ALLOWED_LANGS for p in parts) or not val  # keep if blank (US list is mostly English)

def read_rules(path: Path) -> List[str]:
    if not path.exists():
        return []
    rules = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            rules.append(s)
    return rules

def parse_rule(rule: str):
    # attr=VALUE      (exact, case-insensitive)
    m = re.match(r'^([A-Za-z0-9\-]+)=(.+)$', rule)
    if m:
        return ("attr_exact", m.group(1).lower(), m.group(2))
    # attr~/REGEX/    (regex, case-insensitive)
    m = re.match(r'^([A-Za-z0-9\-]+)~/(.+)/$', rule)
    if m:
        return ("attr_regex", m.group(1).lower(), m.group(2))
    # substring anywhere (tvg-id, tvg-name, display, group-title)
    return ("any_substr", "", rule)

def item_matches(item: Dict, parsed_rule) -> bool:
    mode, attr, pat = parsed_rule
    attrs = item["attrs"]
    any_hay = " || ".join([
        attrs.get("tvg-id", ""),
        attrs.get("tvg-name", ""),
        attrs.get("group-title", ""),
        item["display"],
    ])

    if mode == "attr_exact":
        val = attrs.get(attr, "") if attr != "display" else item["display"]
        return norm(val) == norm(pat)
    if mode == "attr_regex":
        val = attrs.get(attr, "") if attr != "display" else item["display"]
        try:
            return re.search(pat, val or "", re.I) is not None
        except re.error:
            return False
    if mode == "any_substr":
        return norm(pat) in norm(any_hay)
    return False

def apply_lists(items: List[Dict], allow_rules: List[str], deny_rules: List[str]) -> List[Dict]:
    allow_parsed = [parse_rule(r) for r in allow_rules]
    deny_parsed  = [parse_rule(r) for r in deny_rules]
    out = []
    for it in items:
        if not lang_ok(it):
            continue
        # Allowlist: if provided, item must match at least one
        if allow_parsed:
            if not any(item_matches(it, r) for r in allow_parsed):
                continue
        # Denylist: drop if matches any
        if deny_parsed and any(item_matches(it, r) for r in deny_parsed):
            continue
        out.append(it)
    return out

def prefer(items: List[Dict]) -> List[Dict]:
    # Prefer https over http, and .m3u8 over other URLs for duplicates (by id/name/display)
    buckets = {}
    def key(it):
        return (it["attrs"].get("tvg-id") or it["attrs"].get("tvg-name") or it["display"]).lower()
    for it in items:
        buckets.setdefault(key(it), []).append(it)
    result = []
    for _, lst in buckets.items():
        # Choose best URL in each bucket
        def score(u):
            s = 0
            if PREFER_HTTPS and u.lower().startswith("https://"): s += 2
            if PREFER_M3U8 and u.lower().endswith(".m3u8"):      s += 1
            return s
        best = max(lst, key=lambda x: score(x["url"] or ""))
        result.append(best)
    return result

def dedup(items: List[Dict], key: str) -> List[Dict]:
    if key not in ("tvg-id", "tvg-name", "none"):
        key = "tvg-id"
    if key == "none":
        return items
    seen = set()
    out = []
    for it in items:
        val = it["attrs"].get(key, "") or (it["display"] if key == "tvg-name" else "")
        k = norm(val)
        if not k:  # fallback to display if missing id
            k = norm(it["display"])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out

def write_m3u(items: List[Dict], path: Path):
    lines = ["#EXTM3U"]
    for it in items:
        attrs = it["attrs"].copy()
        # keep some common attrs tidy
        parts = []
        for k in ("tvg-id","tvg-name","tvg-chno","tvg-language","tvg-country","group-title","tvg-logo"):
            v = attrs.get(k)
            if v:
                parts.append(f'{k}="{v}"')
        lines.append(f'#EXTINF:-1 {" ".join(parts)},{it["display"]}')
        lines.append(it["url"] or "")
    path.write_text("\n".join(lines), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(description="Filter the iptv-org US M3U into your own list.")
    ap.add_argument("--source", default=US_M3U_URL, help="Source M3U URL or local file path")
    ap.add_argument("--allow", default=ALLOWLIST_FILE, help="Allowlist file (optional). If present, only matching channels kept.")
    ap.add_argument("--deny",  default=DENYLIST_FILE,  help="Denylist file (optional).")
    ap.add_argument("--out",   default=OUTPUT_FILE,    help="Output M3U file path")
    ap.add_argument("--no-english-only", action="store_true", help="Do not filter by English language")
    ap.add_argument("--dedup", default=DEDUP_KEY, help="Dedup by tvg-id|tvg-name|none")
    args = ap.parse_args()

    global ENGLISH_ONLY
    if args.no_english_only:
        ENGLISH_ONLY = False

    raw = fetch_text(args.source) if re.match(r"^https?://", args.source, re.I) else Path(args.source).read_text(encoding="utf-8", errors="ignore")
    items = tokenize_m3u(raw)

    allow_rules = read_rules(Path(args.allow))
    deny_rules  = read_rules(Path(args.deny))

    filtered = apply_lists(items, allow_rules, deny_rules)
    filtered = prefer(filtered)
    filtered = dedup(filtered, args.dedup)

    write_m3u(filtered, Path(args.out))
    print(f"Kept {len(filtered)} channels. Wrote {args.out}")

if __name__ == "__main__":
    main()
