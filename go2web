#!/usr/bin/env python3
import argparse
import json
import os
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import quote_plus

_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")


def _load_cache():
    if os.path.exists(_CACHE_FILE):
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)



class _TextExtractor(HTMLParser):
    # Only non-void elements whose *content* should be suppressed.
    # Void elements (meta, link, br, img …) have no closing tag, so including
    # them in SKIP_TAGS would permanently raise _skip_depth with no way to
    # lower it again, causing all subsequent text to be swallowed.
    SKIP_TAGS = {"script", "style", "noscript", "head"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, _attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def _decode_chunked(body):
    """Reassemble a chunked transfer-encoded body into a plain string."""
    result = []
    # Work on bytes to avoid any multi-byte character splitting at chunk edges
    data = body.encode("utf-8", errors="surrogateescape")
    while data:
        # Find the CRLF that terminates the chunk-size line
        crlf = data.find(b"\r\n")
        if crlf == -1:
            break
        size_token = data[:crlf].split(b";")[0].strip()
        if not size_token:
            break
        try:
            chunk_size = int(size_token, 16)
        except ValueError:
            break
        if chunk_size == 0:
            break
        start = crlf + 2
        result.append(data[start : start + chunk_size])
        data = data[start + chunk_size + 2:]  # skip chunk data + trailing CRLF
    return b"".join(result).decode("utf-8", errors="replace")


class _SearchResultParser(HTMLParser):
    """Extract result titles and URLs from DuckDuckGo's HTML search page."""

    def __init__(self):
        super().__init__()
        self.results = []          # list of (title, url)
        self._in_result_link = False
        self._current_url = None
        self._current_title_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attr_dict = dict(attrs)
            cls = attr_dict.get("class", "")
            href = attr_dict.get("href", "")
            if "result__a" in cls and href:
                self._in_result_link = True
                self._current_url = href
                self._current_title_parts = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_result_link:
            self._in_result_link = False
            title = "".join(self._current_title_parts).strip()
            if title and self._current_url:
                self.results.append((title, self._current_url))
            self._current_url = None
            self._current_title_parts = []

    def handle_data(self, data):
        if self._in_result_link:
            self._current_title_parts.append(data)


def parse_response(raw_response):
    # Split headers from body on the blank line
    if "\r\n\r\n" in raw_response:
        headers, body = raw_response.split("\r\n\r\n", 1)
    elif "\n\n" in raw_response:
        headers, body = raw_response.split("\n\n", 1)
    else:
        headers, body = "", raw_response

    # Decode chunked transfer encoding if indicated by the headers
    if "transfer-encoding: chunked" in headers.lower():
        body = _decode_chunked(body)

    # Pretty-print JSON responses
    if "content-type: application/json" in headers.lower():
        try:
            return json.dumps(json.loads(body), indent=2)
        except json.JSONDecodeError:
            return body.strip()

    extractor = _TextExtractor()
    extractor.feed(body)
    text = "".join(extractor.parts)

    # Collapse runs of blank lines down to a single blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_url(url):
    """Return (scheme, host, port, path) for an http/https URL."""
    if url.startswith("https://"):
        scheme, default_port, rest = "https", 443, url[8:]
    elif url.startswith("http://"):
        scheme, default_port, rest = "http", 80, url[7:]
    else:
        raise ValueError(f"Unsupported scheme in URL: {url}")

    slash_idx = rest.find("/")
    if slash_idx == -1:
        host_part, path = rest, "/"
    else:
        host_part, path = rest[:slash_idx], rest[slash_idx:]

    if ":" in host_part:
        host, port = host_part.rsplit(":", 1)
        port = int(port)
    else:
        host, port = host_part, default_port

    return scheme, host, port, path


def _do_request(scheme, host, port, path):
    """Open a socket, send a GET request, and return the raw response string."""
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Connection: close\r\n"
        f"Accept: application/json, text/html\r\n"
        f"Accept-Encoding: identity\r\n"
        f"\r\n"
    )

    raw_sock = socket.create_connection((host, port), timeout=10)
    if scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(raw_sock, server_hostname=host)
    else:
        sock = raw_sock

    try:
        sock.sendall(request.encode("utf-8"))
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()

    return b"".join(chunks).decode("utf-8", errors="replace")


def fetch_url(url, _max_redirects=10):
    REDIRECT_CODES = {"301", "302", "303", "307", "308"}
    cache = _load_cache()

    if url in cache:
        print("[cache hit] serving from cache")
        return cache[url]

    print("[cache miss] fetching from network")
    original_url = url
    for _ in range(_max_redirects):
        scheme, host, port, path = _parse_url(url)
        raw = _do_request(scheme, host, port, path)

        # Read the status line
        first_line_end = raw.find("\r\n")
        if first_line_end == -1:
            break
        status_parts = raw[:first_line_end].split(None, 2)
        if len(status_parts) < 2 or status_parts[1] not in REDIRECT_CODES:
            # Not an HTTP redirect — check for JS/meta-refresh redirect in body
            headers_end = raw.find("\r\n\r\n")
            body = raw[headers_end + 4:] if headers_end != -1 else raw
            headers_block = raw[:headers_end] if headers_end != -1 else ""
            if "transfer-encoding: chunked" in headers_block.lower():
                body = _decode_chunked(body)

            # window.location.replace("url") or window.parent.location.replace("url")
            js_match = re.search(r'location\.replace\(["\']([^"\']+)["\']', body)
            if js_match:
                url = js_match.group(1)
                continue

            # <meta http-equiv="refresh" content="0;url=...">
            meta_match = re.search(r'(?i)<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\']?\d+;\s*url=([^"\'>\s]+)', body)
            if meta_match:
                url = meta_match.group(1)
                continue

            cache[original_url] = raw
            _save_cache(cache)
            return raw  # no redirect found — return as-is

        # Extract Location header
        headers_end = raw.find("\r\n\r\n")
        headers_block = raw[:headers_end] if headers_end != -1 else raw
        location = None
        for line in headers_block.split("\r\n")[1:]:
            if line.lower().startswith("location:"):
                location = line[len("location:"):].strip()
                break

        if not location:
            cache[original_url] = raw
            _save_cache(cache)
            return raw  # redirect with no Location — give up

        # Resolve relative redirects (e.g. /path or //host/path)
        if location.startswith("//"):
            location = scheme + ":" + location
        elif location.startswith("/"):
            location = f"{scheme}://{host}{'' if port in (80, 443) else f':{port}'}{location}"

        url = location

    raise RuntimeError(f"Exceeded maximum redirects ({_max_redirects})")


def search(term):
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(term)}"
    raw = fetch_url(url)

    if "\r\n\r\n" in raw:
        headers, body = raw.split("\r\n\r\n", 1)
    elif "\n\n" in raw:
        headers, body = raw.split("\n\n", 1)
    else:
        headers, body = "", raw

    if "transfer-encoding: chunked" in headers.lower():
        body = _decode_chunked(body)

    parser = _SearchResultParser()
    parser.feed(body)

    results = parser.results[:10]
    if not results:
        print("No results found.")
        return

    for i, (title, url) in enumerate(results, 1):
        print(f"{i}. {title}")
        print(f"   {url}")

    print()
    try:
        choice = input("Enter a number to open a result (or press Enter to quit): ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    if not choice:
        return

    if not choice.isdigit() or not (1 <= int(choice) <= len(results)):
        print(f"Invalid choice. Please enter a number between 1 and {len(results)}.")
        return

    _, selected_url = results[int(choice) - 1]
    if selected_url.startswith("//"):
        selected_url = "https:" + selected_url
    print()
    print(parse_response(fetch_url(selected_url)))


def main():
    parser = argparse.ArgumentParser(
        prog="go2web",
        description="A simple web fetcher and search tool",
        add_help=True,
    )
    parser.add_argument("-u", metavar="URL", help="fetch the specified URL")
    parser.add_argument("-s", metavar="SEARCH_TERM", help="search for the given term")

    args = parser.parse_args()

    if args.u:
        raw = fetch_url(args.u)
        print(parse_response(raw))
    elif args.s:
        search(args.s)
    else:
        print("Use go2web -h for help")


if __name__ == "__main__":
    main()