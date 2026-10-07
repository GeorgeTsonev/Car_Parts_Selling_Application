#!/usr/bin/env python3
"""Fill logo_url / picture_url / photo_url in vehicles.json from Wikimedia Commons.
Usage:  python add_images.py vehicles.json vehicles_images.json --contact you@example.com
        add --brand Acura (repeatable) to test on a few brands first
pip install requests
"""
import argparse, json, os, re, time
import requests

API = "https://commons.wikimedia.org/w/api.php"
DELAY = 0.4          # ~2.5 req/s, well under the unauthenticated limits
THUMB = 800
CACHE_FILE = "image_cache.json"
cache = json.load(open(CACHE_FILE)) if os.path.exists(CACHE_FILE) else {}
sess = requests.Session()


def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def search(query, kind, brand, model=None):
    """Return best {'url','title','license','author','page'} or None."""
    key = f"{kind}|{query}"
    if key in cache:
        return cache[key]
    ftype = "drawing" if kind == "logo" else "bitmap"
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:{ftype}", "gsrnamespace": 6, "gsrlimit": 10,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 400 if kind == "logo" else THUMB,
    }
    time.sleep(DELAY)
    for attempt in range(4):
        r = sess.get(API, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    pages = sorted((r.json().get("query", {}).get("pages", {})).values(),
                   key=lambda p: p.get("index", 99))
    best = None
    for p in pages:
        title = norm(p["title"])
        info = (p.get("imageinfo") or [{}])[0]
        if norm(brand) not in title:
            continue
        if kind != "logo" and model and norm(model) not in title:
            continue
        if kind == "logo" and "logo" not in title:
            continue
        if kind != "logo" and (info.get("width", 0) < 1000 or "logo" in title):
            continue
        meta = info.get("extmetadata", {})
        best = {
            "url": info.get("thumburl") or info.get("url"),
            "title": p["title"],
            "license": strip_html(meta.get("LicenseShortName", {}).get("value")),
            "author": strip_html(meta.get("Artist", {}).get("value")),
            "page": info.get("descriptionurl"),
        }
        break
    cache[key] = best
    json.dump(cache, open(CACHE_FILE, "w"), ensure_ascii=False)
    return best

def load_vehicles(path):
    raw = json.load(open(path, encoding="utf-8-sig"))
    if isinstance(raw, dict) and "brands" in raw:
        return raw
    if not isinstance(raw, list):
        raise SystemExit(f"Unexpected format: {type(raw).__name__}")

    def to_int(v):
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            return None

    tree = {}
    for r in raw:
        brand, model = (r.get("brand") or "").strip(), (r.get("model") or "").strip()
        if not brand or not model:
            continue
        gen_no = to_int(r.get("gen")) or 0
        mod = (r.get("mod") or "").strip()
        name = f"Gen {gen_no}" if (not mod or mod.lower() == model.lower()) and gen_no else (mod or model)
        g = {"name": name}
        s, e = to_int(r.get("start_year")), to_int(r.get("end_year"))
        if s:
            g["start_year"] = s
        if e:
            g["end_year"] = e
        tree.setdefault(brand, {}).setdefault(model, []).append((gen_no, g))

    return {"type": "vehicles", "format_version": 1, "brands": [
        {"name": b, "models": [
            {"name": m, "generations": [g for _, g in sorted(tree[b][m], key=lambda x: x[0])]}
            for m in sorted(tree[b])]}
        for b in sorted(tree)]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--contact", required=True, help="email or URL for the User-Agent")
    ap.add_argument("--brand", action="append")
    ap.add_argument("--skip-generations", action="store_true",
                    help="only logos + model pictures (far fewer requests)")
    a = ap.parse_args()
    sess.headers["User-Agent"] = f"VehicleImageFiller/1.0 ({a.contact}) python-requests"

    data = load_vehicles(a.src)
    wanted = {b.lower() for b in a.brand} if a.brand else None
    credits = []

    def apply(node, field, hit, label):
        if hit and hit["url"]:
            node[field] = hit["url"]
            credits.append({"item": label, "field": field, **hit})

    for b in data["brands"]:
        if wanted and b["name"].lower() not in wanted:
            continue
        print("Brand:", b["name"])
        apply(b, "logo_url", search(f'{b["name"]} logo', "logo", b["name"]), b["name"])
        for m in b["models"]:
            apply(m, "picture_url",
                  search(f'{b["name"]} {m["name"]}', "model", b["name"], m["name"]),
                  f'{b["name"]} {m["name"]}')
            if a.skip_generations:
                continue
            for g in m["generations"]:
                q = f'{b["name"]} {m["name"]} {g.get("start_year", "")}'.strip()
                apply(g, "photo_url", search(q, "gen", b["name"], m["name"]),
                      f'{b["name"]} {m["name"]} {g.get("name", "")}')
        json.dump(data, open(a.dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump(credits, open("image_attribution.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()