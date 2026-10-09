#!/usr/bin/env python3
"""洛中の通り(名前が「〜通/小路/大路」の道路)を Overpass API から取得し、
static/tools/kyoto-street/streets.json に保存する。

自分のPCで一度だけ実行するためのスクリプト(標準ライブラリのみ)。

  python3 scripts/kyoto-street/build_streets.py

範囲は引数で変えられる(既定は 北=今出川 南=九条 東=鴨川 西=西大路 あたり)。

  python3 scripts/kyoto-street/build_streets.py --north 35.031 --south 34.980 --west 135.730 --east 135.774

混雑で失敗したときは、待たずに同じコマンドをもう一度実行すればよい(取得済みのタイルは
scripts/kyoto-street/.cache/ に保存してあり、続きから取る)。取り直したいときは .cache を消す。

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
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = "yutamoty-com-kyoto-street/1.0 (https://yutamoty.com/)"

SCALE = 100000           # 1e-5度単位の整数で持つ
SIMPLIFY_TOLERANCE_M = 1.0   # 折れ線の簡略化の許容誤差
QUERY_MARGIN = (0.004, 0.005)  # 範囲の外側にこれだけ余分に取る(縁の交点を取りこぼさないため)
R_LAT, R_LON = 110540, 111320

NAME_RE = re.compile(r"(通|小路|大路)$")
UNSAFE_RE = re.compile(r"[<>&\"'`\\\x00-\x1f]")  # HTML や制御に使われる文字を含む名前は採用しない(OSMは誰でも編集できる)
LOOSE_RE = "通|小路|大路"   # 表記ゆれの確認用に、名前のどこかに含む道路まで広く取る


def normalize(name):
    """OSMの表記ゆれを寄せる(全角半角・空白・「〜通り」)。"""
    n = unicodedata.normalize("NFKC", name).strip()
    n = re.sub(r"\s+", "", n)
    if n.endswith("通り"):
        n = n[:-1]
    return n


def build_query(box):
    """1タイル分の問い合わせ。box は (南, 西, 北, 東)。"""
    bbox = "%f,%f,%f,%f" % box
    return (
        "[out:json][timeout:90];("
        'way["highway"]["name"~"%s"](%s);'
        'way["highway"]["name:ja"~"%s"](%s);'
        ");out tags geom qt;" % (LOOSE_RE, bbox, LOOSE_RE, bbox)
    )


def make_tiles(bounds, rows, cols):
    """範囲(余白込み)を rows x cols のタイルに分ける。小さな問い合わせにして時間切れを避ける。"""
    s, w, n, e = bounds
    ms, mw = QUERY_MARGIN
    s, w, n, e = s - ms, w - mw, n + ms, e + mw
    tiles = []
    for i in range(rows):
        for j in range(cols):
            tiles.append((s + (n - s) * i / rows, w + (e - w) * j / cols,
                          s + (n - s) * (i + 1) / rows, w + (e - w) * (j + 1) / cols))
    return tiles


def is_cert_error(ex):
    reason = getattr(ex, "reason", ex)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def fetch_tile(query, endpoints, dead, wait, max_attempts):
    """1タイルを取得する。混雑(429/504など)なら待って再試行し、接続先を順に替える。
    証明書が合わない接続先は検証を外さずに候補から外す。"""
    last = None
    for attempt in range(max_attempts):
        live = [ep for ep in endpoints if ep not in dead]
        if not live:
            break
        ep = live[attempt % len(live)]
        host = urllib.parse.urlparse(ep).netloc
        req = urllib.request.Request(
            ep,
            data=("data=" + urllib.parse.quote(query)).encode(),
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=150) as res:
                data = json.loads(res.read().decode("utf-8"))
            if "elements" not in data:
                raise ValueError("elements がない応答")
            if "runtime error" in str(data.get("remark", "")):
                raise ValueError("サーバー側の時間切れ/メモリ不足: %s" % data["remark"])
            return data
        except urllib.error.HTTPError as ex:
            last = ex
            if ex.code == 400:
                raise SystemExit("問い合わせが不正でした(HTTP 400): %s" % ex)
            delay = min(wait * (2 ** attempt), 120)
            print("  %s: HTTP %s(%d秒待って再試行)" % (host, ex.code, delay), file=sys.stderr)
        except Exception as ex:  # noqa: BLE001 - 次の接続先・再試行へ回すため広く受ける
            last = ex
            if is_cert_error(ex):
                dead.add(ep)
                print("  %s: 証明書が合わないため、この接続先は使いません" % host, file=sys.stderr)
                continue
            delay = min(wait * (2 ** attempt), 120)
            print("  %s: %s(%d秒待って再試行)" % (host, ex, delay), file=sys.stderr)
        time.sleep(delay)
    raise SystemExit("取得できませんでした: %s\n時間をおいて、同じコマンドをもう一度実行してください(取得済みの分は保存してあり、続きから取ります)。" % last)


def fetch_all(bounds, args):
    """タイルごとに取得して結合する。取得済みのタイルは cache-dir から読み、再実行で続きから進める。"""
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    endpoints = args.endpoint or ENDPOINTS
    tiles = make_tiles(bounds, args.rows, args.cols)
    dead, seen, elements = set(), set(), []
    for k, box in enumerate(tiles, 1):
        f = cache / ("%.4f_%.4f_%.4f_%.4f.json" % box)
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            print("タイル %d/%d: 保存済みを使用" % (k, len(tiles)), file=sys.stderr)
        else:
            print("タイル %d/%d を取得中" % (k, len(tiles)), file=sys.stderr)
            data = fetch_tile(build_query(box), endpoints, dead, args.wait, args.attempts)
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(args.pause)  # 相手に負荷をかけないよう、続けて投げない
        for el in data["elements"]:
            key = (el.get("type"), el.get("id"))
            if el.get("id") is not None and key in seen:
                continue  # 複数のタイルにまたがる道路は一度だけ
            seen.add(key)
            elements.append(el)
    return {"elements": elements}


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
        if not NAME_RE.search(name) or UNSAFE_RE.search(name):
            rejected[raw] = rejected.get(raw, 0) + 1
            continue
        pts = [(g["lat"], g["lon"]) for g in el["geometry"]]
        for run in split_inside(pts, box):
            enc = encode(simplify(run, SIMPLIFY_TOLERANCE_M))
            if enc:
                streets.setdefault(name, []).append(enc)
    return streets, rejected


def default_output():
    """スクリプトがリポジトリの中にあれば、実行した場所に関係なくサイトの置き場所へ出す。
    ダウンロードしたスクリプトを単体で動かすときは、実行した場所に streets.json を出す。"""
    repo = Path(__file__).resolve().parents[2]
    if (repo / "hugo.toml").exists():
        return str(repo / "static" / "tools" / "kyoto-street" / "streets.json")
    return str(Path.cwd() / "streets.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--north", type=float, default=35.031, help="北端の緯度(既定: 今出川通のすこし北)")
    ap.add_argument("--south", type=float, default=34.980, help="南端の緯度(既定: 九条通あたり)")
    ap.add_argument("--west", type=float, default=135.730, help="西端の経度(既定: 西大路通あたり)")
    ap.add_argument("--east", type=float, default=135.774, help="東端の経度(既定: 鴨川・川端通あたり)")
    ap.add_argument("--input", help="取得済みの Overpass 応答(JSON)。指定すると通信しない")
    ap.add_argument("--output", default=default_output(), help="出力先(既定: リポジトリ内なら static/tools/kyoto-street/streets.json、それ以外は実行した場所の streets.json)")
    ap.add_argument("--rows", type=int, default=4, help="範囲を南北に分ける数(既定: 4)")
    ap.add_argument("--cols", type=int, default=3, help="範囲を東西に分ける数(既定: 3)")
    ap.add_argument("--endpoint", action="append", help="Overpass の接続先(複数指定可)。省略すると既定の候補を順に使う")
    ap.add_argument("--cache-dir", default=str(Path(__file__).resolve().parent / ".cache"), help="タイルごとの取得結果の保存先")
    ap.add_argument("--wait", type=float, default=10, help="混雑時の待ち時間の基準(秒)。再試行のたびに倍にする")
    ap.add_argument("--attempts", type=int, default=6, help="1タイルあたりの試行回数の上限")
    ap.add_argument("--pause", type=float, default=2, help="タイルの間にあける時間(秒)")
    args = ap.parse_args()

    bounds = (args.south, args.west, args.north, args.east)
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)  # 取得してから書けないと分かるのを避ける
    print("出力先: %s" % out, file=sys.stderr)
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = fetch_all(bounds, args)
    print("取得した道路(way): %d 本" % len(data["elements"]), file=sys.stderr)

    streets, rejected = process(data["elements"], bounds)
    doc = {
        "version": 1,
        "generated": datetime.date.today().isoformat(),
        "bounds": list(bounds),
        "attribution": "© OpenStreetMap contributors (ODbL)",
        "streets": [{"n": k, "w": v} for k, v in sorted(streets.items())],
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))

    size = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    ways = sum(len(v) for v in streets.values())
    pts = sum(len(x) // 2 for v in streets.values() for x in v)
    print("通り %d 種 / 折れ線 %d 本 / 点 %d 個 / %.0f KB → %s" % (len(streets), ways, pts, size / 1024, out), file=sys.stderr)

    if rejected:
        print("\n名前に「通/小路/大路」を含むが、通りとして採用しなかった道路(表記ゆれの確認用):", file=sys.stderr)
        for name, c in sorted(rejected.items(), key=lambda kv: -kv[1]):
            print("  %s ×%d" % (name, c), file=sys.stderr)


if __name__ == "__main__":
    main()
