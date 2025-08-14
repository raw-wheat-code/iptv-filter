#!/usr/bin/env python3
"""
Local exact matcher for iptv-org/epg:

- Reads tvg-id values from channels.m3u
- Scans all **/*.channels.xml under a local epg repo
- Emits channels_list.xml:

  <?xml version="1.0" encoding="UTF-8"?>
  <channels>
  <channel site="..." lang="..." xmltv_id="..." site_id="...">Name</channel>
  ...
  </channels>

Usage:
  python get_channels_list.py --m3u "D:\Repos\iptv-filter\channels.m3u" --epg-dir "D:\Repos\epg" [--out channels_list.xml]
"""

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from typing import Iterable, List, Set, Dict, Tuple
from xml.sax.saxutils import escape as xml_escape

EXTINF_TVG_ID_RE = re.compile(r'tvg-id="([^"]+)"')

def read_tvg_ids_from_m3u(m3u_path: str) -> List[str]:
    """Extract tvg-id attributes from #EXTINF lines, preserving order and deduping."""
    if not os.path.isfile(m3u_path):
        raise FileNotFoundError(f"M3U not found: {m3u_path}")
    tvg_ids: List[str] = []
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("#EXTINF"):
                m = EXTINF_TVG_ID_RE.search(line)
                if m:
                    val = m.group(1).strip()
                    if val:
                        tvg_ids.append(val)
    # de-dupe preserving order
    seen: Set[str] = set()
    uniq: List[str] = []
    for x in tvg_ids:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def find_channels_files(epg_dir: str) -> List[str]:
    """Return list of all *.channels.xml files under epg_dir."""
    if not os.path.isdir(epg_dir):
        raise NotADirectoryError(f"EPG directory not found: {epg_dir}")
    pattern = os.path.join(epg_dir, "**", "*.channels.xml")
    files = glob.glob(pattern, recursive=True)
    return files

def parse_channels_file(xml_path: str) -> Iterable[ET.Element]:
    """Yield <channel> elements from a channels.xml file. Skip malformed files."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        return root.findall(".//channel")
    except ET.ParseError as e:
        print(f"[WARN] XML parse error in {xml_path}: {e}", file=sys.stderr)
        return []

def render_channel_line(el: ET.Element) -> str:
    """
    Render a <channel> line with attributes in the exact order:
    site, lang, xmltv_id, site_id, then text content as element body.
    """
    def attr(name: str) -> str:
        return (el.attrib.get(name) or "").strip()

    def esc(v: str) -> str:
        return xml_escape(v, {'"': "&quot;", "'": "&apos;"})

    site     = esc(attr("site"))
    lang     = esc(attr("lang"))
    xmltv_id = esc(attr("xmltv_id"))
    site_id  = esc(attr("site_id"))
    name     = esc((el.text or "").strip())

    return f'<channel site="{site}" lang="{lang}" xmltv_id="{xmltv_id}" site_id="{site_id}">{name}</channel>'

def collect_matches(tvg_ids: Set[str], channels_files: List[str]) -> Tuple[List[str], Set[str]]:
    """
    Scan all channels files once. Return rendered lines and set of tvg_ids not found.
    Exact, case-sensitive match on xmltv_id.
    """
    found_ids: Set[str] = set()
    lines: List[str] = []

    for path in channels_files:
        for ch in parse_channels_file(path):
            xmltv_id = (ch.attrib.get("xmltv_id") or "").strip()
            if xmltv_id and xmltv_id in tvg_ids:
                lines.append(render_channel_line(ch))
                found_ids.add(xmltv_id)

    not_found = tvg_ids - found_ids
    return lines, not_found

def write_channels_list_xml(lines: List[str], out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write("<channels>\n")
        for ln in lines:
            f.write(f"{ln}\n")
        f.write("</channels>\n")

def main():
    ap = argparse.ArgumentParser(description="Build channels_list.xml from local iptv-org/epg and an M3U tvg-id list.")
    ap.add_argument("--m3u", required=True, help="Full path to channels.m3u")
    ap.add_argument("--epg-dir", required=True, help="Path to local clone of iptv-org/epg")
    ap.add_argument("--out", default="channels_list.xml", help="Output XML file path")
    args = ap.parse_args()

    tvg_ids = read_tvg_ids_from_m3u(args.m3u)
    if not tvg_ids:
        print("No tvg-id values found in the provided M3U.", file=sys.stderr)
        sys.exit(2)

    files = find_channels_files(args.epg_dir)
    if not files:
        print("No *.channels.xml files found under the EPG directory.", file=sys.stderr)
        sys.exit(3)

    lines, not_found = collect_matches(set(tvg_ids), files)
    write_channels_list_xml(lines, args.out)

    print(f"Wrote {args.out} with {len(lines)} matched entries from {len(files)} files.")
    if not_found:
        print("\nThe following tvg-id values had no exact xmltv_id match:")
        for x in sorted(not_found):
            print(f"  - {x}")

if __name__ == "__main__":
    main()
