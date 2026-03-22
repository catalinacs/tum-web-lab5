import argparse
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import quote_plus



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

    extractor = _TextExtractor()
    extractor.feed(body)
    text = "".join(extractor.parts)

    # Collapse runs of blank lines down to a single blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_url(url):
    # Parse scheme
    if url.startswith("https://"):
        scheme = "https"
        default_port = 443
        rest = url[len("https://"):]
    elif url.startswith("http://"):
        scheme = "http"
        default_port = 80
        rest = url[len("http://"):]
    else:
        raise ValueError(f"Unsupported scheme in URL: {url}")

    # Split host[:port] from path
    slash_idx = rest.find("/")
    if slash_idx == -1:
        host_part = rest
        path = "/"
    else:
        host_part = rest[:slash_idx]
        path = rest[slash_idx:]

    # Split optional port from host
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_part
        port = default_port

    # Build HTTP/1.1 request
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )

    # Open raw TCP socket
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


def search(term):
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(term)}"
    raw = fetch_url(url)

    if "\r\n\r\n" in raw:
        _, body = raw.split("\r\n\r\n", 1)
    elif "\n\n" in raw:
        _, body = raw.split("\n\n", 1)
    else:
        body = raw

    parser = _SearchResultParser()
    parser.feed(body)

    results = parser.results[:10]
    if not results:
        print("No results found.")
        return

    for i, (title, url) in enumerate(results, 1):
        print(f"{i}. {title}")
        print(f"   {url}")


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
        parser.print_help()


if __name__ == "__main__":
    main()