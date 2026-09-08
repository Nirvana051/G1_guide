"""Render a Markdown file to a self-contained styled HTML page and serve it.

Usage:
    python3 tools/md_serve.py <path.md> [port]

A no-dependency Markdown renderer (the dev box has no `markdown`/`pandoc`),
covering the features README_G1_Deploy.md actually uses: headings, fenced code
blocks, tables, lists/checkboxes, blockquotes, inline code/bold/links.
"""

from __future__ import annotations

import html as _html
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_CSS = """
:root{--bg:#0f1420;--fg:#e6ebf5;--muted:#8b96ad;--border:#273350;
--accent:#4f9cff;--code:#0d1424;--ok:#34c07c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
line-height:1.6;font-size:15px}
.wrap{max-width:880px;margin:0 auto;padding:32px 20px 96px}
h1{font-size:26px;border-bottom:2px solid var(--border);padding-bottom:12px}
h2{font-size:21px;margin-top:34px;border-bottom:1px solid var(--border);padding-bottom:8px}
h3{font-size:17px;margin-top:26px;color:#cfe1ff}
h4{font-size:15px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{background:#182238;padding:1px 6px;border-radius:4px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.9em;color:#ffd479}
pre{background:var(--code);border:1px solid var(--border);border-radius:8px;
padding:14px;overflow-x:auto}
pre code{background:none;padding:0;color:#cfe1ff;font-size:13px;display:block}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}
th,td{border:1px solid var(--border);padding:7px 10px;text-align:left;vertical-align:top}
th{background:#1c2538;color:#cfe1ff}
tr:nth-child(even) td{background:#131a2b}
blockquote{border-left:4px solid #f0a03c;margin:14px 0;padding:8px 16px;background:#241f12;border-radius:0 8px 8px 0;color:#ffe0a3}
ul,ol{padding-left:26px}
li{margin:4px 0}
li.task{list-style:none;margin-left:-22px}
li.task input{margin-right:8px}
hr{border:none;border-top:1px solid var(--border);margin:28px 0}
"""


def _inline(text: str) -> str:
    text = _html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img src="\2" alt="\1">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _split_row(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    return len(cells) > 0 and all(re.fullmatch(r":?-{3,}:?", c or "-") for c in cells)


def _flush_list(items, ordered, out):
    tag = "ol" if ordered else "ul"
    out.append("<%s>" % tag)
    for text in items:
        task = re.match(r"^\[( |x|X)\]\s+(.*)$", text)
        if task:
            checked = task.group(1).lower() == "x"
            body = _inline(task.group(2))
            out.append(
                '<li class="task"><input type="checkbox" disabled%s>%s</li>'
                % (" checked" if checked else "", body)
            )
        else:
            out.append("<li>%s</li>" % _inline(text))
    out.append("</%s>" % tag)


def render_markdown(md: str) -> str:
    lines = md.split("\n")
    out = []
    i, n = 0, len(lines)
    in_code = False
    code_buf = []
    code_lang = ""

    while i < n:
        line = lines[i]

        if line.startswith("```"):
            if not in_code:
                in_code = True
                code_lang = line[3:].strip()
                code_buf = []
            else:
                lang = ' class="lang-%s"' % code_lang if code_lang else ""
                out.append("<pre><code%s>%s</code></pre>" % (lang, _html.escape("\n".join(code_buf))))
                in_code = False
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2)), level))
            i += 1
            continue

        if re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", line):
            out.append("<hr>")
            i += 1
            continue

        if "|" in line and i + 1 < n and _is_separator(lines[i + 1]):
            header = _split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            out.append("<table><thead><tr>")
            for h in header:
                out.append("<th>%s</th>" % _inline(h))
            out.append("</tr></thead><tbody>")
            for row in rows:
                out.append("<tr>")
                for c in row:
                    out.append("<td>%s</td>" % _inline(c))
                out.append("</tr>")
            out.append("</tbody></table>")
            continue

        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % _inline(" ".join(buf)))
            continue

        list_match = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if list_match:
            ordered = bool(re.match(r"\d+[.)]", list_match.group(2)))
            items = []
            marker = list_match.group(2)
            while i < n:
                lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if not lm or (bool(re.match(r"\d+[.)]", lm.group(2))) != ordered):
                    break
                items.append(lm.group(3))
                i += 1
            _flush_list(items, ordered, out)
            continue

        if line.strip() == "":
            i += 1
            continue

        buf = [line]
        i += 1
        while i < n and lines[i].strip() != "":
            nxt = lines[i]
            if (nxt.startswith(("#", "```", ">", "|"))
                    or re.match(r"^\s*([-*+]|\d+[.)])\s+", nxt)
                    or re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", nxt)):
                break
            buf.append(nxt)
            i += 1
        out.append("<p>%s</p>" % _inline(" ".join(buf)))

    return "\n".join(out)


class _Handler(BaseHTTPRequestHandler):
    page = b""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self._page())

    def _page(self):
        return self.page

    def log_message(self, *args):  # silence access log
        pass


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python3 tools/md_serve.py <path.md> [port]")
    path = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8090
    with open(path, "r", encoding="utf-8") as fh:
        md = fh.read()
    title = path.rsplit("/", 1)[-1]
    body = render_markdown(md)
    html_page = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s</title><style>%s</style></head>"
        "<body><div class='wrap'><h1>%s</h1>%s</div></body></html>"
        % (title, _CSS, _html.escape(title).replace(".md", ""), body)
    )
    _Handler.page = html_page.encode("utf-8")
    server = HTTPServer(("127.0.0.1", port), _Handler)
    print("serving %s on http://127.0.0.1:%d/  (Ctrl+C to stop)" % (path, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
