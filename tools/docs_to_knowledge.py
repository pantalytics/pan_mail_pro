#!/usr/bin/env python3
"""Render `docs/` into the article bodies of the Odoo knowledge base.

    pip install markdown                 # once
    tools/docs_to_knowledge.py           # HTML per article id, on stdout
    tools/docs_to_knowledge.py 120       # one article

`docs/` is the source. The published copy lives in the Pantalytics knowledge
base at https://pantalytics.odoo.com/knowledge/article/116, one article per
file, and Odoo has no importer: the body is written through the ORM. So this
script does the half a machine can do, which is the conversion and the
cross-links, and leaves the write to whoever has the credentials.

Change a page here, run this, paste the body onto its article. The mapping
below is the only place the article ids live.
"""
import posixpath
import re
import sys
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent.parent / "docs"
BASE = "https://pantalytics.odoo.com/knowledge/article/"

# docs/<path> -> knowledge.article id. Add a page here when you add a page.
PAGES = {
    "README.md": 116,
    "getting-started/installation.md": 117,
    # No article yet: create it in Knowledge and put its id here. Until then
    # rendering a page that links to it stops with a message, on purpose.
    "getting-started/connect-pantalytics.md": None,
    "getting-started/azure-setup.md": 118,
    "getting-started/google-setup.md": 128,
    "getting-started/imap-setup.md": 129,
    "getting-started/user-setup.md": 119,
    "configuration/mailboxes.md": 120,
    "configuration/incoming-sync.md": 121,
    "configuration/where-mail-lands.md": 130,
    "troubleshooting.md": 122,
    "security.md": 123,
}

LINK = re.compile(r"\]\(([^)\s]+\.md)(#[^)]*)?\)")
BULLET = re.compile(r"^(\s*)[-*] ")
ITEM = re.compile(r"^\s*(?:[-*] |\d+\. )")
ORDERED = re.compile(r"^\d+\. ")
FIRST_ITEM = re.compile(r"^\s*1\. ")


def normalize(text):
    """Make the docs' CommonMark list shapes survive python-markdown.

    Three things CommonMark accepts and python-markdown does not: a 3-space
    continuation indent, a bullet list glued to the line above, and an ordered
    list interrupting a paragraph. All three are formatting rather than
    content, so they are fixed here rather than by reflowing every page.
    """
    text = re.sub(r"^   (?! )", "    ", text, flags=re.M)
    out = []
    for line in text.split("\n"):
        prev = out[-1] if out else ""
        if BULLET.match(line) and prev.strip() and not ITEM.match(prev):
            out.append("")
        elif ORDERED.match(line) and prev.strip() and prev.startswith((" ", "\t")):
            out.append("")
        elif FIRST_ITEM.match(line) and prev.strip() and not ITEM.match(prev):
            out.append("")
        out.append(line)
    return "\n".join(out)


def rewrite_links(text, src):
    """Point every link between two pages at the article that page became."""
    def repl(match):
        target = posixpath.normpath(posixpath.join(posixpath.dirname(src), match.group(1)))
        if target not in PAGES:
            sys.exit(f"{src}: link to {match.group(1)}, which is not in PAGES")
        if PAGES[target] is None:
            sys.exit(f"{src}: link to {match.group(1)}, which has no knowledge article yet; "
                     "create one and put its id in PAGES")
        return f"]({BASE}{PAGES[target]})"
    return LINK.sub(repl, text)


def convert(src):
    text = rewrite_links(normalize((DOCS / src).read_text()), src)
    html = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    # Knowledge renders the article name itself, so the page title would be a
    # second heading above the body.
    return re.sub(r"^<h1>.*?</h1>\s*", "", html, count=1, flags=re.S)


def main():
    wanted = sys.argv[1:]
    for src, article in PAGES.items():
        if article is None or (wanted and str(article) not in wanted):
            continue
        print(f"===== {article}  {src}  {BASE}{article}")
        print(convert(src))


if __name__ == "__main__":
    main()
