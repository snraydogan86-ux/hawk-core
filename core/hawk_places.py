import os
import requests
from typing import Optional, Dict, Any

GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def _sort_results(results):
    return sorted(
        results,
        key=lambda x: (
            bool((x.get("opening_hours") or {}).get("open_now", False)),
            float(x.get("rating") or 0),
            int(x.get("user_ratings_total") or 0),
        ),
        reverse=True,
    )


def _normalize_top3(results):
    top_results = []
    for place in results[:3]:
        top_results.append({
            "name": place.get("name"),
            "address": place.get("vicinity") or place.get("formatted_address"),
            "rating": place.get("rating"),
            "open_now": (place.get("opening_hours") or {}).get("open_now"),
        })
    if not top_results:
        return None

    best = top_results[0]
    return {
        "ok": True,
        "name": best.get("name"),
        "address": best.get("address"),
        "rating": best.get("rating"),
        "open_now": best.get("open_now"),
        "results": top_results,
    }


def hawk_places_search(query: str, lat: float = None, lng: float = None, place_type: str = None) -> Optional[Dict[str, Any]]:
    try:
        if not GOOGLE_API_KEY:
            return None

        has_real_geo = lat is not None and lng is not None

        if has_real_geo:
            url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            params = {
                "location": f"{lat},{lng}",
                "radius": 2500,
                "key": GOOGLE_API_KEY,
            }
            if place_type:
                params["keyword"] = place_type
            else:
                params["keyword"] = query
        else:
            url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
            params = {
                "query": query,
                "key": GOOGLE_API_KEY,
            }

        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        data = r.json() or {}

        results = data.get("results") or []
        if not results:
            return None

        results = _sort_results(results)
        return _normalize_top3(results)

    except Exception:
        return None
