#!/usr/bin/env python3
"""Add photo_url to a limited sample of generations.
Usage: python add_gen_photos.py vehicles.json vehicles_gen.json --contact you@example.com \
         --per-brand 5 [--brand Acura --brand Seat] [--max-total 100]
"""
import argparse, json
import add_images as ai


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--contact", required=True)
    ap.add_argument("--brand", action="append")
    ap.add_argument("--per-brand", type=int, default=5, help="max generations tried per brand")
    ap.add_argument("--max-total", type=int, default=100, help="max generations tried overall")
    a = ap.parse_args()
    ai.sess.headers["User-Agent"] = f"VehicleImageFiller/1.0 ({a.contact}) python-requests"

    data = ai.load_vehicles(a.src)
    wanted = {b.lower() for b in a.brand} if a.brand else None
    credits, tried_total, found = [], 0, 0

    for b in data["brands"]:
        if wanted and b["name"].lower() not in wanted:
            continue
        tried = 0
        for m in b["models"]:
            for g in m["generations"]:
                if tried >= a.per_brand or tried_total >= a.max_total:
                    break
                if g.get("photo_url"):
                    continue
                tried += 1
                tried_total += 1
                q = f'{b["name"]} {m["name"]} {g.get("start_year", "")}'.strip()
                hit = ai.search(q, "gen", b["name"], m["name"])
                if hit and hit["url"]:
                    g["photo_url"] = hit["url"]
                    found += 1
                    credits.append({"item": f'{b["name"]} {m["name"]} {g.get("name", "")}',
                                    "field": "photo_url", **hit})
                    print("OK  ", q)
                else:
                    print("none", q)
            if tried >= a.per_brand or tried_total >= a.max_total:
                break
        if tried_total >= a.max_total:
            break

    json.dump(data, open(a.dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(credits, open("image_attribution_generations.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"Done: {found} photos from {tried_total} generations tried")


if __name__ == "__main__":
    main()