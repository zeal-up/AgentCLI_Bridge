#!/usr/bin/env python3
"""Build a polished, self-contained HTML docs site from the Markdown sources.

Reads README.md + docs/*.md, converts each (tables, fenced code, syntax
highlighting via Pygments, TOC), wraps them in a single-page site with a
sticky sidebar nav (scrollspy), mobile hamburger, client-side search, and
light/dark theme. Output: docs_html/index.html (no external dependencies).

Run: python3 docs_html/build.py
"""
from __future__ import annotations
import html
import os
import re
from pathlib import Path

import markdown
from pygments.formatters import HtmlFormatter


def pygments_css() -> str:
    """Token-color CSS for codehilite, light + dark (media-scoped). Background
    is stripped so our own .codehilite/pre background tokens win in both themes."""
    def defs(style: str) -> str:
        css = HtmlFormatter(style=style).get_style_defs(".codehilite")
        # drop the .codehilite background/color rule (we set our own)
        css = re.sub(r"^\.codehilite\s*\{[^}]*\}\s*", "", css, flags=re.M)
        return css
    light = defs("default")
    dark = defs("monokai")
    return light + "\n@media (prefers-color-scheme: dark){\n" + dark + "\n}\n"


ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "index.html"

# (source file, sidebar title, icon)
DOCS = [
    ("README.md",                "概览",          "📖"),
    ("docs/INSTALL.md",          "安装指南(人工)",  "📦"),
    ("docs/INSTALL_FOR_AGENT.md","安装(交给 Agent)", "🤖"),
    ("docs/DESIGN.md",           "架构设计",       "🏗️"),
    ("docs/REQUIREMENTS.md",     "需求",          "📋"),
    ("docs/PLAN.md",             "实施计划",       "🗺️"),
    ("docs/M5-injector-spec.md", "Injector 规格",  "⚙️"),
]

CSS = """
:root{
  --bg:#fafbfc; --bg-soft:#f1f3f5; --card:#fff; --fg:#1f2328; --fg-mut:#57606a;
  --border:#d8dee4; --accent:#0969da; --accent-soft:#ddf4ff; --code-bg:#f6f8fa;
  --code-fg:#1f2328; --warn:#bf8700; --warn-bg:#fff8c5; --ok:#1a7f37; --ok-bg:#dafbe1;
  --shadow:0 6px 24px rgba(0,0,0,.08); --sidebar-w:280px;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#0d1117; --bg-soft:#161b22; --card:#161b22; --fg:#c9d1d9; --fg-mut:#8b949e;
    --border:#30363d; --accent:#58a6ff; --accent-soft:#0d2d4f; --code-bg:#0d1117; --code-fg:#c9d1d9;
    --warn:#d29922; --warn-bg:#3b2c10; --ok:#3fb950; --ok-bg:#0d2818; --shadow:0 6px 24px rgba(0,0,0,.4);}
}
*{box-sizing:border-box}
html{scroll-behavior:smooth; scroll-padding-top:72px}
body{margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",Helvetica,Arial,sans-serif; background:var(--bg); color:var(--fg); line-height:1.7; font-size:15.5px}
a{color:var(--accent); text-decoration:none}
a:hover{text-decoration:underline}

/* top bar */
.topbar{position:fixed; top:0; left:0; right:0; height:56px; z-index:50; background:var(--card); border-bottom:1px solid var(--border); display:flex; align-items:center; gap:12px; padding:0 16px}
.topbar .brand{font-weight:700; font-size:16px; white-space:nowrap}
.topbar .brand .v{color:var(--fg-mut); font-weight:400; font-size:12px; margin-left:6px}
.menu-btn{display:none; background:none; border:1px solid var(--border); border-radius:6px; padding:4px 8px; cursor:pointer; color:var(--fg); font-size:18px}
.search{flex:1; max-width:380px; margin-left:auto}
.search input{width:100%; padding:6px 10px; border-radius:6px; border:1px solid var(--border); background:var(--bg-soft); color:var(--fg); font-size:13px}
.search input:focus{outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft)}
.theme-hint{color:var(--fg-mut); font-size:12px; white-space:nowrap}

/* layout */
.layout{display:flex; max-width:1400px; margin:56px auto 0; }
.sidebar{width:var(--sidebar-w); flex-shrink:0; position:sticky; top:56px; height:calc(100vh - 56px); overflow-y:auto; padding:20px 12px 40px; border-right:1px solid var(--border)}
.sidebar nav{display:flex; flex-direction:column; gap:2px}
.doc-group{margin-bottom:10px}
.doc-group > .gtitle{display:flex; align-items:center; gap:8px; padding:6px 10px; font-weight:600; border-radius:6px; color:var(--fg)}
.doc-group > .gtitle .ic{font-size:15px}
.doc-group > ul{list-style:none; padding:0 0 0 26px; margin:4px 0 0}
.doc-group > ul li{margin:1px 0}
.doc-group a.sub{display:block; padding:4px 8px; border-radius:5px; color:var(--fg-mut); font-size:13px; line-height:1.35}
.doc-group a.sub:hover{background:var(--bg-soft); color:var(--fg); text-decoration:none}
.doc-group a.sub.active{background:var(--accent-soft); color:var(--accent); font-weight:600}
.content{flex:1; min-width:0; padding:32px 40px 80px; max-width:920px}
.content section.doc{margin-bottom:56px; padding-top:8px; border-top:2px solid var(--border)}
.content section.doc:first-child{border-top:none}
.content section.doc > .doc-anchor-target{scroll-margin-top:72px}

/* typography */
h1,h2,h3,h4{scroll-margin-top:72px; line-height:1.3; font-weight:700}
h1{font-size:27px; margin:0 0 18px; padding-bottom:10px; border-bottom:1px solid var(--border)}
h2{font-size:22px; margin:32px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--border)}
h3{font-size:18px; margin:24px 0 10px}
h4{font-size:15px; margin:18px 0 8px; color:var(--fg-mut)}
p{margin:10px 0}
ul,ol{margin:10px 0; padding-left:26px}
li{margin:4px 0}
code{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace; font-size:13px; background:var(--code-bg); padding:2px 6px; border-radius:4px; color:var(--code-fg)}
pre{background:var(--code-bg); border:1px solid var(--border); border-radius:8px; padding:14px 16px; overflow:auto; margin:14px 0; box-shadow:var(--shadow)}
pre code{background:none; padding:0; font-size:12.5px; line-height:1.5}
blockquote{margin:14px 0; padding:10px 16px; border-left:4px solid var(--accent); background:var(--accent-soft); border-radius:0 6px 6px 0; color:var(--fg)}
table{border-collapse:collapse; width:100%; margin:14px 0; font-size:13.5px; display:block; overflow-x:auto}
th,td{border:1px solid var(--border); padding:8px 12px; text-align:left; vertical-align:top}
th{background:var(--bg-soft); font-weight:600}
tr:nth-child(even) td{background:var(--bg-soft)}
hr{border:none; border-top:1px solid var(--border); margin:28px 0}
img{max-width:100%}

/* codehilite overrides: use our theme tokens so it works in dark mode */
div.highlight{background:var(--code-bg) !important; border-radius:8px; margin:14px 0}
div.highlight pre{margin:0; border:none; box-shadow:none}
.codehilite .vc,.codehilite .nd{color:inherit}
.codehilite .hll{background:rgba(255,255,255,.06)}

/* mobile */
@media (max-width: 900px){
  .menu-btn{display:inline-block}
  .sidebar{position:fixed; left:0; top:56px; z-index:40; background:var(--card); transform:translateX(-100%); transition:transform .2s; box-shadow:var(--shadow)}
  .sidebar.open{transform:translateX(0)}
  .content{padding:20px 16px 60px; max-width:100%}
  .search{max-width:none}
  .theme-hint{display:none}
  table{font-size:12.5px}
}
.back-to-top{position:fixed; bottom:20px; right:20px; width:40px; height:40px; border-radius:50%; background:var(--card); border:1px solid var(--border); box-shadow:var(--shadow); display:flex; align-items:center; justify-content:center; cursor:pointer; color:var(--fg); z-index:30; opacity:0; transition:opacity .2s}
.back-to-top.show{opacity:1}
"""

JS = """
const sidebar = document.querySelector('.sidebar');
document.querySelector('.menu-btn').addEventListener('click', () => sidebar.classList.toggle('open'));
// Close sidebar on nav click (mobile)
sidebar.addEventListener('click', e => { if(e.target.closest('a') && window.innerWidth <= 900) sidebar.classList.remove('open'); });

// Scrollspy: highlight the nav item for the section currently in view.
const headings = Array.from(document.querySelectorAll('.content h2[id], .content h3[id]'));
const navLinks = Array.from(document.querySelectorAll('.sidebar a.sub'));
const byId = {};
navLinks.forEach(a => { const h = a.getAttribute('href'); if(h && h.startsWith('#')) byId[h.slice(1)] = a; });
const setActive = id => { navLinks.forEach(a => a.classList.remove('active')); if(byId[id]) byId[id].classList.add('active'); };
const io = new IntersectionObserver(entries => {
  entries.forEach(e => { if(e.isIntersecting) setActive(e.target.id); });
}, {rootMargin: '-72px 0px -70% 0px'});
headings.forEach(h => io.observe(h));

// Search: filter nav items by text.
const sinput = document.querySelector('.search input');
sinput.addEventListener('input', () => {
  const q = sinput.value.trim().toLowerCase();
  document.querySelectorAll('.doc-group').forEach(g => {
    let any = false;
    g.querySelectorAll('a.sub').forEach(a => {
      const m = !q || a.textContent.toLowerCase().includes(q);
      a.style.display = m ? '' : 'none'; if(m) any = true;
    });
    const gtitle = g.querySelector('.gtitle');
    const gmatch = !q || gtitle.textContent.toLowerCase().includes(q);
    g.style.display = (any || gmatch) ? '' : 'none';
  });
});

// Back-to-top.
const btt = document.querySelector('.back-to-top');
window.addEventListener('scroll', () => btt.classList.toggle('show', window.scrollY > 600));
btt.addEventListener('click', () => window.scrollTo({top:0, behavior:'smooth'}));
"""


def slugify(text: str) -> str:
    """Match markdown toc extension's slug behaviour for anchor ids."""
    s = re.sub(r"[^\w一-鿿 -]", "", text.strip().lower())
    return re.sub(r"\s+", "-", s) or "section"


def convert(md_path: Path) -> tuple[str, list[tuple[int, str, str]]]:
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "codehilite", "toc", "attr_list"],
        extension_configs={
            "codehilite": {"guess_lang": True, "noclasses": False, "css_class": "codehilite"},
            "toc": {"permalink": "🔗", "permalink_class": "anchor"},
        },
    )
    body = md.convert(md_path.read_text(encoding="utf-8"))
    # toc_tokens: list of {id, name, level, children}
    toc_tree = getattr(md, "toc_tokens", [])
    items = []
    def walk(nodes, depth=0):
        for n in nodes:
            items.append((n.get("level", depth) or depth, n.get("name", ""), n.get("id", "")))
            walk(n.get("children", []), depth+1)
    walk(toc_tree)
    # keep only h2 (level 2) for sidebar sub-items
    subs = [(lv, name, id_) for (lv, name, id_) in items if lv == 2]
    return body, subs


def prefix_anchors(body: str, prefix: str) -> str:
    """Make every in-doc heading id / internal anchor unique across docs by
    prefixing with the doc id. The markdown toc extension slugs Chinese-only
    headings to empty -> falls back to _1,_2 which collide across docs."""
    body = re.sub(r'id="([^"]+)"', lambda m: f'id="{prefix}-{m.group(1)}"', body)
    body = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{prefix}-{m.group(1)}"', body)
    return body


def build() -> None:
    sections = []
    sidebar_groups = []
    for rel, title, icon in DOCS:
        path = ROOT / rel
        if not path.exists():
            continue
        body, subs = convert(path)
        doc_id = "doc-" + slugify(title)
        body = prefix_anchors(body, doc_id)
        sections.append(
            f'<section class="doc" id="{doc_id}">\n'
            f'<div class="doc-anchor-target"></div>\n'
            f'{body}\n</section>'
        )
        sub_html = "".join(
            f'<li><a class="sub" href="#{doc_id}-{html.escape(id_)}">{html.escape(name)}</a></li>'
            for (_, name, id_) in subs
        )
        sidebar_groups.append(
            f'<div class="doc-group">'
            f'<div class="gtitle"><span class="ic">{icon}</span>'
            f'<a href="#{doc_id}" style="color:inherit">{html.escape(title)}</a></div>'
            f'<ul>{sub_html}</ul></div>'
        )

    sidebar = f'<nav>{"".join(sidebar_groups)}</nav>'
    html_out = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentCLI Bridge — 文档</title>
<style>{CSS}
{pygments_css()}</style>
</head>
<body>
<div class="topbar">
  <button class="menu-btn" aria-label="菜单">☰</button>
  <div class="brand">AgentCLI Bridge<span class="v">文档</span></div>
  <div class="search"><input type="search" placeholder="搜索目录…" aria-label="搜索目录"></div>
  <div class="theme-hint">跟随系统明暗</div>
</div>
<div class="layout">
  <aside class="sidebar">{sidebar}</aside>
  <main class="content">
{''.join(sections)}
  </main>
</div>
<button class="back-to-top" aria-label="回到顶部">↑</button>
<script>{JS}</script>
</body>
</html>
"""
    OUT.write_text(html_out, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    build()
