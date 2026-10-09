#!/usr/bin/env python3
"""洛中の通り(名前が「〜通/小路/大路」の道路)を Overpass API から取得し、
static/tools/kyoto-street/streets.json に保存する。

自分のPCで一度だけ実行するためのスクリプト(標準ライブラリのみ)。

  python3 scripts/kyoto-street/build_streets.py

範囲は引数で変えられる(既定は 北=今出川 南=九条 東=鴨川 西=西大路 あたり)。

  python3 scripts/kyoto-street/build_streets.py --north 35.031 --south 34.980 --west 135.730 --east 135.774

すでに取得した Overpass の応答(JSON)がある場合は、通信せずに加工だけできる。

  python3 scripts/kyoto-street/build_streets.py --input overpass.json

出力形式(streets.json):
  {
    "version": 1,
    "generated": "YYYY-MM-DD",
    "bounds": [南, 西, 北, 東],         # 判定の対象範囲(この外は「碁盤の目の外」)
    "attribution": "© OpenStreetMap contributors (ODbL)",
    "streets": [
      { "n": "烏丸通",
        "w": [ [lat0, lon0, dlat1, dlon1, ...], ... ] }   # 1way=1本の折れ線
    ]
  }
座標は 1e-5 度(約1m)の整数。各折れ線は先頭だけ絶対値、以降は直前の点との差分。
"""
import argparse
import datetime
import json
import math
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.jp/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
USER_AGENT = "yutamoty-com-kyoto-street/1.0 (https://yutamoty.com/)"

SCALE = 100000           # 1e-5度単位の整数で持つ
SIMPLIFY_TOLERANCE_M = 1.0   # 折れ線の簡略化の許容誤差
QUERY_MARGIN = (0.004, 0.005)  # 範囲の外側にこれだけ余分に取る(縁の交点を取りこぼさないため)
R_LAT, R_LON = 110540, 111320

NAME_RE = re.compile(r"(通|小路|大路)$")
LOOSE_RE = "通|小路|大路"   # 表記ゆれの確認用に、名前のどこかに含む道路まで広く取る


def normalize(name):
    """OSMの表記ゆれを寄せる(全角半角・空白・「〜通り」)。"""
    n = unicodedata.normalize("NFKC", name).strip()
    n = re.sub(r"\s+", "", n)
    if n.endswith("通り"):
        n = n[:-1]
    return n


def build_query(s, w, n, e):
    ms, mw = QUERY_MARGIN
    bbox = "%f,%f,%f,%f" % (s - ms, w - mw, n + ms, e + mw)
    return (
        "[out:json][timeout:240];("
        'way["highway"]["name"~"%s"](%s);'
        'way["highway"]["name:ja"~"%s"](%s);'
        ");out geom;" % (LOOSE_RE, bbox, LOOSE_RE, bbox)
    )


def fetch(query):
    last = None
    for ep in ENDPOINTS:
        print("取得中:", ep, file=sys.stderr)
        req = urllib.request.Request(
            ep,
            data=("data=" + urllib.parse.quote(query)).encode(),
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as res:
                data = json.loads(res.read().decode("utf-8"))
            if "elements" not in data:
                raise ValueError("elements がない応答")
            return data
        except Exception as ex:  # noqa: BLE001 - 次の接続先へ回すため広く受ける
            last = ex
            print("  失敗: %s(30秒待って次へ)" % ex, file=sys.stderr)
            time.sleep(30)
    raise SystemExit("どの接続先でも取得できませんでした: %s" % last)


def to_xy(lat, lon, lat0, lon0):
    return ((lon - lon0) * math.cos(math.radians(lat0)) * R_LON, (lat - lat0) * R_LAT)


def simplify(pts, tol):
    """Douglas-Peucker。pts は (lat, lon)。端点は必ず残す。"""
    if len(pts) < 3:
        return pts
    lat0, lon0 = pts[0]
    xy = [to_xy(la, lo, lat0, lon0) for la, lo in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        far, far_d = -1, -1.0
        for i in range(a + 1, b):
            px, py = xy[i]
            if seg == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dx * (ay - py) - (ax - px) * dy) / seg
            if d > far_d:
                far, far_d = i, d
        if far_d > tol:
            keep[far] = True
            stack.append((a, far))
            stack.append((far, b))
    return [p for p, k in zip(pts, keep) if k]


def split_inside(pts, box):
    """範囲(南,西,北,東)の内側に連続して収まる区間だけを取り出す。"""
    s, w, n, e = box
    runs, cur = [], []
    for la, lo in pts:
        if s <= la <= n and w <= lo <= e:
            cur.append((la, lo))
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def encode(pts):
    """(lat, lon) の列 → 先頭は絶対値、以降は差分の整数列。重複点は落とす。"""
    ints = [(round(la * SCALE), round(lo * SCALE)) for la, lo in pts]
    dedup = [ints[0]]
    for p in ints[1:]:
        if p != dedup[-1]:
            dedup.append(p)
    if len(dedup) < 2:
        return None
    out = [dedup[0][0], dedup[0][1]]
    for (a, b), (c, d) in zip(dedup, dedup[1:]):
        out += [c - a, d - b]
    return out


def process(elements, bounds):
    s, w, n, e = bounds
    ms, mw = QUERY_MARGIN
    box = (s - ms, w - mw, n + ms, e + mw)
    streets = {}
    rejected = {}
    for el in elements:
        if el.get("type") != "way" or not el.get("geometry") or not el.get("tags"):
            continue
        raw = el["tags"].get("name:ja") or el["tags"].get("name")
        if not raw:
            continue
        name = normalize(raw)
        if not NAME_RE.search(name):
            rejected[raw] = rejected.get(raw, 0) + 1
            continue
        pts = [(g["lat"], g["lon"]) for g in el["geometry"]]
        for run in split_inside(pts, box):
            enc = encode(simplify(run, SIMPLIFY_TOLERANCE_M))
            if enc:
                streets.setdefault(name, []).append(enc)
    return streets, rejected


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--north", type=float, default=35.031, help="北端の緯度(既定: 今出川通のすこし北)")
    ap.add_argument("--south", type=float, default=34.980, help="南端の緯度(既定: 九条通あたり)")
    ap.add_argument("--west", type=float, default=135.730, help="西端の経度(既定: 西大路通あたり)")
    ap.add_argument("--east", type=float, default=135.774, help="東端の経度(既定: 鴨川・川端通あたり)")
    ap.add_argument("--input", help="取得済みの Overpass 応答(JSON)。指定すると通信しない")
    ap.add_argument("--output", default="static/tools/kyoto-street/streets.json")
    args = ap.parse_args()

    bounds = (args.south, args.west, args.north, args.east)
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = fetch(build_query(*bounds))
    print("取得した道路(way): %d 本" % len(data["elements"]), file=sys.stderr)

    streets, rejected = process(data["elements"], bounds)
    doc = {
        "version": 1,
        "generated": datetime.date.today().isoformat(),
        "bounds": list(bounds),
        "attribution": "© OpenStreetMap contributors (ODbL)",
        "streets": [{"n": k, "w": v} for k, v in sorted(streets.items())],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))

    size = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    ways = sum(len(v) for v in streets.values())
    pts = sum(len(x) // 2 for v in streets.values() for x in v)
    print("通り %d 種 / 折れ線 %d 本 / 点 %d 個 / %.0f KB → %s" % (len(streets), ways, pts, size / 1024, args.output), file=sys.stderr)

    if rejected:
        print("\n名前に「通/小路/大路」を含むが、通りとして採用しなかった道路(表記ゆれの確認用):", file=sys.stderr)
        for name, c in sorted(rejected.items(), key=lambda kv: -kv[1]):
            print("  %s ×%d" % (name, c), file=sys.stderr)


if __name__ == "__main__":
    main()
