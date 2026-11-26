"""Match excluded evidence across retrieval results and research runs."""

from urllib.parse import parse_qsl, urlencode, urlsplit


def _location(value: str) -> str:
    value = value.strip()
    try:
        url = urlsplit(value)
        if url.scheme in ("http", "https") and url.hostname:
            query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
                     if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid", "spm"}]
            port = url.port
            host = url.hostname.lower()
            if port and (url.scheme, port) not in {("http", 80), ("https", 443)}:
                host += f":{port}"
            return f"{host}{url.path.rstrip('/')}?{urlencode(sorted(query))}"
    except ValueError:
        pass
    return value.split("#", 1)[0]


def is_excluded(item: dict, exclusions: list[dict]) -> bool:
    if item.get("source_type") == "sql":
        text = str(item.get("text", ""))
        sql = " ".join(item.get("sql", "").split())
        return any(rule["sql"] and sql == " ".join(rule["sql"].split()) and text == rule["text"]
                   for rule in exclusions)
    location = _location(item.get("url") or item.get("source") or item.get("title") or "")
    chunk = item.get("chunk_id")
    return any(
        not rule["sql"] and (
            (rule["location"] and location == _location(rule["location"]))
            or (rule["chunk_id"] and chunk is not None and str(chunk) == rule["chunk_id"])
        )
        for rule in exclusions
    )
