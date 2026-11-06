#!/usr/bin/env python3
"""Render the DMM docs site for GitHub Pages.

    python3 _build/build.py [path/to/DMM_Architecture.md] [--updated YYYY-MM-DD]

Writes index.html (landing) and architecture/index.html at the branch root.
Defaults to the snapshot in _build/DMM_Architecture.md. Needs markdown-it-py
and Pygments. Jekyll does not serve underscore directories, so _build/ stays
out of the published site.
"""
import argparse
import datetime
import html
import re
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO_URL = "https://github.com/aashayarora/rucio-sense-dmm"
PAGES_URL = "https://aashayarora.github.io/rucio-sense-dmm"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.esm.min.mjs"
FORMATTER = HtmlFormatter(nowrap=True)


# --------------------------------------------------------------------------- markdown

def slugify(text, seen):
    """GitHub-style heading anchor, so the doc's own TOC links keep working."""
    slug = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    base, n = slug, 1
    while slug in seen:
        slug, n = f"{base}-{n}", n + 1
    seen.add(slug)
    return slug


def heading_ids(state):
    seen, toc = set(), []
    for i, tok in enumerate(state.tokens):
        if tok.type != "heading_open":
            continue
        inline = state.tokens[i + 1]
        text = "".join(c.content for c in inline.children or [] if c.type in ("text", "code_inline"))
        anchor = slugify(text, seen)
        tok.attrSet("id", anchor)
        toc.append((int(tok.tag[1]), anchor, inline.children))
    state.env["toc"] = toc


def render_fence(self, tokens, idx, options, env):
    tok = tokens[idx]
    lang = tok.info.strip().split()[0] if tok.info.strip() else ""
    code = tok.content
    if lang == "mermaid":
        # Mermaid prints a literal "\n" in most labels; <br/> is the portable line break.
        src = code.replace("\\n", "<br/>")
        return f'<figure class="diagram"><pre class="mermaid">{html.escape(src)}</pre></figure>\n'
    try:
        lexer = get_lexer_by_name(lang) if lang else None
    except ClassNotFound:
        lexer = None
    body = highlight(code, lexer, FORMATTER) if lexer else html.escape(code)
    label = f'<span class="code-lang">{html.escape(lang)}</span>' if lang else ""
    return f'<div class="code">{label}<pre><code>{body}</code></pre></div>\n'


def render_heading_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    anchor = tok.attrGet("id")
    link = f'<a class="anchor" href="#{anchor}" aria-label="Link to this section">#</a>'
    return f'<{tok.tag} id="{anchor}">{link}'


def build_md():
    md = MarkdownIt("commonmark", {"html": False, "typographer": False}).enable(["table", "strikethrough"])
    md.core.ruler.push("heading_ids", heading_ids)
    md.add_render_rule("fence", render_fence)
    md.add_render_rule("code_block", render_fence)
    md.add_render_rule("heading_open", render_heading_open)
    md.add_render_rule("table_open", lambda *a: '<div class="table-wrap"><table>\n')
    md.add_render_rule("table_close", lambda *a: "</table></div>\n")
    return md


# --------------------------------------------------------------------------- page chrome

CSS = r"""
:root {
  --bg: #fbfbfa; --surface: #ffffff; --sunken: #f3f3f1; --text: #1f2328; --muted: #5d636b;
  --faint: #8a9099; --border: #e2e2de; --accent: #2457c5; --accent-soft: #e8eefb;
  --callout: #f4f7fd; --callout-edge: #9db5ea;
  --tok-k: #a3218f; --tok-s: #0b7a3e; --tok-c: #6e7781; --tok-n: #0550ae; --tok-f: #8250df; --tok-t: #953800;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111317; --surface: #171a1f; --sunken: #1c2027; --text: #e4e6ea; --muted: #a2a8b1;
    --faint: #737a85; --border: #2b3038; --accent: #7aa7ff; --accent-soft: #1b2a47;
    --callout: #151d2c; --callout-edge: #3b5893;
    --tok-k: #ff8fd8; --tok-s: #7ee2a8; --tok-c: #8b949e; --tok-n: #8cc4ff; --tok-f: #d2a8ff; --tok-t: #ffb77c;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { scroll-padding-top: 4.5rem; -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.65 var(--sans); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.topbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 1rem;
  height: 3.25rem; padding: 0 1.25rem; background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(8px); border-bottom: 1px solid var(--border); }
.brand { font-weight: 650; color: var(--text); letter-spacing: -0.01em; }
.brand span { color: var(--muted); font-weight: 450; }
.topbar nav { margin-left: auto; display: flex; gap: 1.25rem; font-size: 0.9rem; }
.topbar nav a { color: var(--muted); }
.topbar nav a:hover, .topbar nav a[aria-current] { color: var(--text); text-decoration: none; }

.layout { display: grid; grid-template-columns: 17rem minmax(0, 1fr); max-width: 82rem; margin: 0 auto; }
.sidebar { position: sticky; top: 3.25rem; height: calc(100vh - 3.25rem); overflow-y: auto;
  padding: 1.75rem 1rem 3rem 1.25rem; border-right: 1px solid var(--border); font-size: 0.85rem; }
.sidebar h2 { margin: 0 0 0.75rem; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--faint); }
.sidebar ol { list-style: none; margin: 0; padding: 0; }
.sidebar li > a { display: block; padding: 0.22rem 0.6rem; border-radius: 6px; color: var(--muted); line-height: 1.4; }
.sidebar li > a:hover { background: var(--sunken); color: var(--text); text-decoration: none; }
.sidebar li > a.active { background: var(--accent-soft); color: var(--accent); }
.sidebar .l2 { margin-top: 0.6rem; }
.sidebar .l2 > a { color: var(--text); font-weight: 600; }
.sidebar .l3 > a { padding-left: 1.4rem; }
.sidebar code { font-size: 0.95em; background: none; padding: 0; }

main { min-width: 0; padding: 2.5rem 3rem 6rem; }
.doc { max-width: 52rem; }
.doc-head { margin-bottom: 2.5rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }
.doc-head h1 { margin: 0 0 0.5rem; font-size: 2.1rem; line-height: 1.2; letter-spacing: -0.02em; }
.doc-head p { margin: 0; color: var(--muted); font-size: 0.9rem; }

.doc h2 { margin: 3.5rem 0 1rem; padding-top: 0.5rem; font-size: 1.6rem; line-height: 1.25; letter-spacing: -0.015em; }
.doc h3 { margin: 2.5rem 0 0.75rem; font-size: 1.22rem; line-height: 1.3; }
.doc h4 { margin: 2rem 0 0.5rem; font-size: 1.02rem; }
.doc h2, .doc h3, .doc h4 { position: relative; }
.anchor { position: absolute; left: -1.1em; width: 1em; color: var(--faint); opacity: 0; font-weight: 400; }
h2:hover .anchor, h3:hover .anchor, h4:hover .anchor, .anchor:focus { opacity: 1; text-decoration: none; }
.doc p, .doc ul, .doc ol { margin: 0 0 1rem; }
.doc li { margin: 0.2rem 0; }
.doc li > ul, .doc li > ol { margin: 0.2rem 0 0; }
.doc hr { border: 0; border-top: 1px solid var(--border); margin: 2.5rem 0; }
.doc strong { font-weight: 650; }

code { font-family: var(--mono); font-size: 0.86em; background: var(--sunken); padding: 0.12em 0.38em; border-radius: 5px; }
.doc p code, .doc li code { overflow-wrap: anywhere; }
.code { position: relative; margin: 0 0 1.25rem; background: var(--sunken); border: 1px solid var(--border); border-radius: 10px; }
.code pre { margin: 0; padding: 1rem 1.1rem; overflow-x: auto; font: 0.82rem/1.6 var(--mono); }
.code pre code { background: none; padding: 0; font-size: inherit; }
.code-lang { position: absolute; top: 0.4rem; right: 0.6rem; font: 0.68rem var(--mono); color: var(--faint); text-transform: uppercase; letter-spacing: 0.06em; }
.code .k, .code .kn, .code .kd, .code .kr, .code .kp, .code .ow { color: var(--tok-k); }
.code .kc, .code .kt, .code .nb, .code .bp { color: var(--tok-t); }
.code .s, .code .s1, .code .s2, .code .sd, .code .sa, .code .si, .code .se, .code .dl { color: var(--tok-s); }
.code .c, .code .c1, .code .cm, .code .ch, .code .cs, .code .sd { color: var(--tok-c); font-style: italic; }
.code .m, .code .mi, .code .mf, .code .mh, .code .il, .code .nt, .code .na { color: var(--tok-n); }
.code .nf, .code .nc, .code .fm, .code .nd { color: var(--tok-f); }

.table-wrap { margin: 0 0 1.5rem; overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; line-height: 1.5; }
th, td { padding: 0.55rem 0.8rem; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { background: var(--sunken); font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
td code { white-space: nowrap; }

blockquote { margin: 0 0 1.25rem; padding: 0.8rem 1.1rem; background: var(--callout); border-left: 3px solid var(--callout-edge); border-radius: 0 8px 8px 0; }
blockquote p:last-child { margin-bottom: 0; }

.diagram { margin: 0 0 1.5rem; padding: 1.25rem; overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.diagram pre.mermaid { margin: 0; font: 0.78rem/1.5 var(--mono); color: var(--muted); white-space: pre; text-align: center; }
.diagram pre.mermaid[data-processed] { white-space: normal; font-family: var(--sans); color: inherit; }
.diagram svg { display: block; margin: 0 auto; height: auto; }

@media (max-width: 1000px) {
  .layout { display: block; }
  .sidebar { display: none; }
  main { padding: 1.75rem 16px 4rem; }
  .doc-head h1 { font-size: 1.65rem; }
  .doc h2 { font-size: 1.4rem; }
  .anchor { display: none; }
  .topbar { padding: 0 16px; }
  .mobile-hide { display: none; }
}

/* landing */
.landing { max-width: 46rem; margin: 0 auto; padding: 4rem 16px 6rem; }
.landing h1 { margin: 0 0 0.75rem; font-size: 2.4rem; line-height: 1.15; letter-spacing: -0.025em; }
.landing .lede { margin: 0 0 2.5rem; color: var(--muted); font-size: 1.08rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 1rem; margin-bottom: 3rem; }
.card { display: block; padding: 1.2rem 1.3rem; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; color: var(--text); }
.card:hover { border-color: var(--accent); text-decoration: none; }
.card strong { display: block; margin-bottom: 0.3rem; font-size: 1.02rem; }
.card span { color: var(--muted); font-size: 0.9rem; }
.landing h2 { margin: 0 0 0.75rem; font-size: 1.1rem; }
.landing p { margin: 0 0 1rem; }
"""

MERMAID_JS = """
import mermaid from "%s";
const scheme = matchMedia("(prefers-color-scheme: dark)");
const blocks = [...document.querySelectorAll("pre.mermaid")];
blocks.forEach(el => { el.dataset.src = el.textContent; });
async function draw() {
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: scheme.matches ? "dark" : "neutral",
    fontFamily: getComputedStyle(document.body).fontFamily,
    flowchart: { htmlLabels: true, curve: "basis" },
  });
  blocks.forEach(el => { el.removeAttribute("data-processed"); el.textContent = el.dataset.src; });
  await mermaid.run({ nodes: blocks, suppressErrors: true });
}
draw();
scheme.addEventListener("change", draw);
""" % MERMAID_URL

SPY_JS = """
const links = new Map([...document.querySelectorAll(".sidebar a")].map(a => [a.hash.slice(1), a]));
const heads = [...document.querySelectorAll(".doc h2[id], .doc h3[id]")].filter(h => links.has(h.id));
const io = new IntersectionObserver(() => {
  const top = heads.filter(h => h.getBoundingClientRect().top < 120).pop();
  const id = top ? top.id : heads[0] && heads[0].id;
  links.forEach((a, k) => a.classList.toggle("active", k === id));
}, { rootMargin: "0px 0px -70% 0px" });
heads.forEach(h => io.observe(h));
"""


def page(title, description, body, nav_current, extra_head=""):
    def nav(href, label, key):
        cur = ' aria-current="page"' if key == nav_current else ""
        return f'<a href="{href}"{cur}>{label}</a>'
    root = "../" if nav_current == "arch" else "./"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%232457c5'/%3E%3Cpath d='M8 22V10h5.5a6 6 0 010 12H8zm3-3h2.4a3 3 0 000-6H11v6zM19 22V10h3l2 6 2-6h3v12h-3v-6l-2 5-2-5v6h-3z' fill='white'/%3E%3C/svg%3E">
<style>{CSS}</style>
{extra_head}</head>
<body>
<header class="topbar">
  <a class="brand" href="{root}">DMM <span class="mobile-hide">Data Movement Manager</span></a>
  <nav>{nav(root, "Home", "home")}{nav(root + "architecture/", "Architecture", "arch")}{nav(REPO_URL, "GitHub", "gh")}</nav>
</header>
{body}
</body>
</html>
"""


def sidebar(toc, md, env):
    items = []
    for level, anchor, children in toc:
        if level not in (2, 3) or anchor == "table-of-contents":
            continue
        label = md.renderer.renderInline(children, md.options, env)
        items.append(f'<li class="l{level}"><a href="#{anchor}">{label}</a></li>')
    return '<aside class="sidebar"><h2>On this page</h2><ol>' + "".join(items) + "</ol></aside>"


def build_architecture(src, updated):
    text = src.read_text(encoding="utf-8")
    m = re.match(r"\s*#\s+(.+?)\s*\n", text)
    title = m.group(1) if m else "DMM Architecture"
    if m:
        text = text[m.end():]
        text = re.sub(r"\A\s*---\s*\n", "", text)  # rule directly under the title
    md = build_md()
    env = {}
    tokens = md.parse(text, env)
    content = md.renderer.render(tokens, md.options, env)
    meta = f"Last updated {updated.day} {updated:%B %Y} &middot; generated from <code>DMM_Architecture.md</code>"
    body = f"""<div class="layout">
{sidebar(env["toc"], md, env)}
<main>
<article class="doc">
<header class="doc-head"><h1>{html.escape(title)}</h1><p>{meta}</p></header>
{content}
</article>
</main>
</div>
<script type="module">{MERMAID_JS}</script>
<script>{SPY_JS}</script>"""
    out = ROOT / "architecture" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page("DMM Architecture",
                        "Technical reference for the Data Movement Manager: daemons, data models, "
                        "request lifecycle, bandwidth optimisation and SENSE/Rucio/FTS integration.",
                        body, "arch"), encoding="utf-8")
    return out


def build_landing():
    body = f"""<main class="landing">
<h1>Data Movement Manager</h1>
<p class="lede">DMM is the interface layer between Rucio/FTS and SENSE in the Rucio–SENSE
interoperation framework. It provisions SDN circuits for Rucio replication rules and divides
link bandwidth between them, so HEP data flows move over network-aware paths.</p>
<div class="cards">
  <a class="card" href="architecture/"><strong>Architecture reference &rarr;</strong>
    <span>Daemons, data models, request lifecycle, the LP bandwidth optimiser, and the SENSE, Rucio and FTS integrations.</span></a>
  <a class="card" href="{REPO_URL}"><strong>Source on GitHub &rarr;</strong>
    <span>Code, configuration samples, Dockerfile and the Helm chart.</span></a>
</div>
<h2>Helm chart</h2>
<p>This site is also DMM&rsquo;s Helm repository. Create the configuration secrets first
(<code>etc/mksecrets.sh</code>, see the <a href="{REPO_URL}#readme">README</a>), then:</p>
<div class="code"><span class="code-lang">bash</span><pre><code>helm repo add dmm {PAGES_URL}
helm install dmm dmm/dmm</code></pre></div>
</main>"""
    out = ROOT / "index.html"
    out.write_text(page("DMM", "Data Movement Manager (DMM): the Rucio/FTS–SENSE interface layer.", body, "home"),
                   encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", type=Path, default=HERE / "DMM_Architecture.md")
    ap.add_argument("--updated", type=datetime.date.fromisoformat, default=datetime.date.today(),
                    help="date shown as 'Last updated' (default: today)")
    args = ap.parse_args()
    for out in (build_architecture(args.src, args.updated), build_landing()):
        print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
