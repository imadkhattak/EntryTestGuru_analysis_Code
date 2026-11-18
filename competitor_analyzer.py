"""
Competitor analyzer (Playwright + Python)
-----------------------------------------
Crawls a public website, takes full-page screenshots, extracts the content
and features, and builds a PDF report.

Setup:
    pip install playwright pillow
    playwright install chromium

Run:
    python competitor_analyzer.py https://maqsad.io --max-pages 25

Output (in ./output/<domain>/):
    screenshots/   full-page PNGs
    data.json      raw extracted data
    report.html    the report
    report.pdf     the final PDF
"""

import argparse
import base64
import html
import json
import re
import time
import urllib.robotparser
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

Image.MAX_IMAGE_PIXELS = None

# True  = browser runs hidden in the background (default)
# False = you can watch Chrome open and move through the site
HEADLESS =  False

SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip",
            ".mp4", ".mp3", ".css", ".js", ".ico", ".xml", ".json")

# Keywords used to auto-detect features. Edit freely for your niche.
FEATURE_KEYWORDS = {
    "Mock tests / exams":        ["mock test", "mock exam", "mock", "full-length test", "practice test"],
    "Past papers":               ["past paper", "previous paper", "past year", "solved paper"],
    "Video lectures":            ["video lecture", "video lessons", "recorded lecture", "video course", "watch lecture"],
    "Live classes":              ["live class", "live session", "live lecture", "live teaching"],
    "MCQ / question bank":       ["question bank", "mcq", "practice questions", "qbank", "chapter-wise"],
    "AI features":               ["ai-powered", "ai powered", "ai tutor", "ai ", "artificial intelligence", "smart recommendation"],
    "Analytics / progress":      ["analytics", "performance report", "progress tracking", "weak areas", "insights", "dashboard"],
    "Leaderboard / ranking":     ["leaderboard", "rank", "compete", "national ranking"],
    "Study plan / schedule":     ["study plan", "study schedule", "planner", "roadmap", "timetable"],
    "Notes / PDFs":              ["notes", "study material", "pdf", "flashcard"],
    "Doubt solving / mentors":   ["doubt", "mentor", "ask a question", "teacher support", "community", "discussion"],
    "Mobile app":                ["play store", "google play", "app store", "download the app", "android app", "ios app"],
    "Free trial / free content": ["free trial", "free demo", "start for free", "free access", "try for free"],
    "Pricing / subscription":    ["pricing", "subscription", "per month", "/month", "enroll now", "buy now", "rs.", "pkr", "discount"],
    "Scholarships / offers":     ["scholarship", "offer", "coupon", "referral"],
    "Testimonials / results":    ["testimonial", "success stories", "selected", "toppers", "reviews", "students trust"],
    "WhatsApp / social":         ["whatsapp", "facebook", "instagram", "youtube", "tiktok", "telegram"],
    "Urdu / bilingual":          ["urdu", "اردو"],
}

# Entry tests relevant to Pakistan (just for detection)
TEST_NAMES = ["MDCAT", "ECAT", "NUMS", "NET", "FAST", "GIKI", "CAT", "SAT", "LCAT",
              "PIEAS", "AKU", "NTS", "UHS", "PMDC", "O Level", "A Level", "CSS", "NAT"]


def clean_url(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc}{path}"


def slugify(url: str) -> str:
    p = urlparse(url)
    s = (p.netloc + p.path).strip("/").replace("/", "_")
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", s)[:90] or "home"


def auto_scroll(page, step=700, pause=0.25, max_steps=80):
    """Scroll slowly so lazy-loaded content/images appear."""
    for _ in range(max_steps):
        at_bottom = page.evaluate(
            "(s) => { window.scrollBy(0, s); return window.innerHeight + window.scrollY >= document.body.scrollHeight - 5; }",
            step,
        )
        time.sleep(pause)
        if at_bottom:
            break
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.5)


EXTRACT_JS = """
() => {
  const txt = el => (el.innerText || '').replace(/\\s+/g, ' ').trim();
  const uniq = a => [...new Set(a.filter(Boolean))];
  const meta = n => (document.querySelector(`meta[name="${n}"], meta[property="${n}"]`) || {}).content || '';
  return {
    title: document.title,
    description: meta('description') || meta('og:description'),
    h1: uniq([...document.querySelectorAll('h1')].map(txt)),
    h2: uniq([...document.querySelectorAll('h2')].map(txt)).slice(0, 40),
    h3: uniq([...document.querySelectorAll('h3')].map(txt)).slice(0, 60),
    buttons: uniq([...document.querySelectorAll('button, a[class*="btn"], a[class*="button"], [role="button"]')]
                  .map(txt).filter(t => t.length > 1 && t.length < 50)).slice(0, 40),
    nav: uniq([...document.querySelectorAll('nav a, header a')].map(a => txt(a) + ' -> ' + a.href)).slice(0, 60),
    links: uniq([...document.querySelectorAll('a[href]')].map(a => a.href)),
    text: document.body ? document.body.innerText.replace(/\\n{3,}/g, '\\n\\n').trim() : '',
    images: document.images.length,
    videos: document.querySelectorAll('video, iframe[src*="youtube"], iframe[src*="vimeo"]').length,
    forms: document.forms.length,
  };
}
"""


def crawl(start_url, out_dir, max_pages, delay, headless, ignore_robots):
    shots_dir = out_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    start = clean_url(start_url)
    domain = urlparse(start).netloc

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{urlparse(start).scheme}://{domain}/robots.txt")
    try:
        rp.read()
    except Exception:
        rp = None

    queue, seen, pages = deque([start]), {start}, []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        )
        page = ctx.new_page()

        while queue and len(pages) < max_pages:
            url = queue.popleft()
            if rp and not ignore_robots and not rp.can_fetch("*", url):
                print(f"[skip: robots.txt] {url}")
                continue

            print(f"[{len(pages)+1}/{max_pages}] {url}")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeout:
                    pass
                auto_scroll(page)
                data = page.evaluate(EXTRACT_JS)
                status = resp.status if resp else None

                shot_path = shots_dir / f"{len(pages)+1:02d}_{slugify(url)}.png"
                page.screenshot(path=str(shot_path), full_page=True)

                data.update(url=url, status=status, screenshot=str(shot_path))
                pages.append(data)

                for link in data["links"]:
                    link = clean_url(urljoin(url, link))
                    p = urlparse(link)
                    if (p.netloc == domain and p.scheme in ("http", "https")
                            and not p.path.lower().endswith(SKIP_EXT)
                            and link not in seen):
                        seen.add(link)
                        queue.append(link)
            except Exception as e:
                print(f"   ! failed: {e}")

            time.sleep(delay)  # be polite

        browser.close()
    return pages, sorted(seen)


def analyze(pages):
    """Keyword-based feature detection across all pages."""
    all_text = " ".join(p["text"].lower() for p in pages)
    features = {}
    for name, kws in FEATURE_KEYWORDS.items():
        hits = [p["url"] for p in pages if any(k in p["text"].lower() for k in kws)]
        features[name] = hits
    tests = {t: all_text.count(t.lower()) for t in TEST_NAMES if re.search(rf"\b{re.escape(t.lower())}\b", all_text)}
    prices = set()
    for p in pages:
        prices.update(re.findall(r"(?:Rs\.?|PKR|\$)\s?[\d,]+(?:\.\d+)?(?:\s?/\s?\w+)?", p["text"]))
    return features, dict(sorted(tests.items(), key=lambda x: -x[1])), sorted(prices)[:40]


def slice_image(path, out_dir, slice_h=1700, width=1100):
    """Cut tall screenshots into readable slices for the PDF."""
    img = Image.open(path).convert("RGB")
    ratio = width / img.width
    img = img.resize((width, int(img.height * ratio)))
    parts = []
    for i, top in enumerate(range(0, img.height, slice_h)):
        crop = img.crop((0, top, width, min(top + slice_h, img.height)))
        fp = out_dir / f"{Path(path).stem}_part{i+1}.jpg"
        crop.save(fp, quality=80)
        parts.append(fp)
    return parts


def build_html(site, pages, features, tests, prices, out_dir):
    e = html.escape
    slices_dir = out_dir / "slices"
    slices_dir.mkdir(exist_ok=True)

    css = """
    body{font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;font-size:12px;line-height:1.5}
    h1{font-size:28px;margin:0 0 4px} h2{font-size:18px;border-bottom:2px solid #2a5bd7;padding-bottom:4px;margin-top:26px}
    h3{font-size:14px;margin:14px 0 4px;color:#2a5bd7} table{border-collapse:collapse;width:100%;margin:8px 0}
    td,th{border:1px solid #ddd;padding:5px 8px;text-align:left;vertical-align:top;font-size:11px}
    th{background:#f0f4ff} .yes{color:#0a7d33;font-weight:bold} .no{color:#b00020;font-weight:bold}
    .page{page-break-before:always} .url{color:#555;word-break:break-all;font-size:11px}
    img{width:100%;border:1px solid #ddd;margin:6px 0;display:block}
    .chip{display:inline-block;background:#eef2ff;border-radius:10px;padding:2px 8px;margin:2px;font-size:11px}
    .box{border:1px dashed #999;padding:8px;min-height:40px;margin:4px 0}
    """

    h = [f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>"]
    h.append(f"<h1>Competitor Analysis: {e(site)}</h1>")
    h.append(f"<p>Generated on {time.strftime('%Y-%m-%d')} &middot; {len(pages)} pages analysed</p>")

    # Feature matrix
    h.append("<h2>1. Feature detection (keyword based)</h2><table><tr><th>Feature</th><th>Found?</th><th>Pages</th></tr>")
    for name, hits in features.items():
        mark = "<span class='yes'>YES</span>" if hits else "<span class='no'>not found</span>"
        h.append(f"<tr><td>{e(name)}</td><td>{mark}</td><td class='url'>{'<br>'.join(e(u) for u in hits[:4])}</td></tr>")
    h.append("</table>")

    if tests:
        h.append("<h2>2. Entry tests mentioned</h2>")
        h.append("".join(f"<span class='chip'>{e(t)} ({c})</span>" for t, c in tests.items()))
    if prices:
        h.append("<h2>3. Prices / amounts seen</h2>")
        h.append("".join(f"<span class='chip'>{e(p)}</span>" for p in prices))

    # Site map / student journey
    h.append("<h2>4. Site structure (what students can navigate to)</h2><table><tr><th>#</th><th>Page</th><th>Main headline</th></tr>")
    for i, p in enumerate(pages, 1):
        h.append(f"<tr><td>{i}</td><td class='url'>{e(p['url'])}</td><td>{e(' | '.join(p['h1'])[:150] or p['title'])}</td></tr>")
    h.append("</table>")

    h.append("<h2>5. Main navigation</h2>")
    if pages:
        h.append("<div>" + "".join(f"<span class='chip'>{e(n.split(' -> ')[0])}</span>" for n in pages[0]["nav"][:40] if n.split(' -> ')[0]) + "</div>")

    # Manual strategy section to fill after reviewing
    h.append("<h2>6. Your analysis (fill in after reviewing the pages)</h2>")
    for q in ["Target audience & positioning", "Business model / monetisation",
              "Student journey (landing -> signup -> prep -> test)", "Strengths",
              "WEAKNESSES / GAPS I can target at entrytestguru.com", "Differentiating features to build"]:
        h.append(f"<h3>{q}</h3><div class='box'></div>")

    # Per-page detail
    for i, p in enumerate(pages, 1):
        h.append(f"<div class='page'><h2>Page {i}: {e(p['title'] or p['url'])}</h2><div class='url'>{e(p['url'])} (HTTP {p['status']})</div>")
        if p["description"]:
            h.append(f"<p><b>Meta description:</b> {e(p['description'])}</p>")
        h.append(f"<p>Images: {p['images']} &middot; Videos/embeds: {p['videos']} &middot; Forms: {p['forms']}</p>")
        if p["h1"]:
            h.append(f"<h3>H1</h3><p>{e(' | '.join(p['h1']))}</p>")
        if p["h2"]:
            h.append("<h3>Section headings</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in p["h2"][:25]) + "</ul>")
        if p["buttons"]:
            h.append("<h3>Buttons / CTAs</h3>" + "".join(f"<span class='chip'>{e(b)}</span>" for b in p["buttons"][:25]))
        h.append(f"<h3>Text excerpt</h3><p>{e(p['text'][:1500])}...</p>")
        for part in slice_image(p["screenshot"], slices_dir):
            b64 = base64.b64encode(part.read_bytes()).decode()
            h.append(f"<img src='data:image/jpeg;base64,{b64}'>")
        h.append("</div>")

    h.append("</body></html>")
    report = out_dir / "report.html"
    report.write_text("\n".join(h), encoding="utf-8")
    return report


def html_to_pdf(report_html, pdf_path, headless=True):
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=headless)
        pg = b.new_page()
        pg.goto(report_html.resolve().as_uri())
        pg.pdf(path=str(pdf_path), format="A4", print_background=True,
               margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"})
        b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="Start URL, e.g. https://maqsad.io")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between pages")
    ap.add_argument("--show-browser", action="store_true")
    ap.add_argument("--respect-robots", action="store_true",
                    help="check robots.txt before visiting pages (off by default)")
    args = ap.parse_args()

    domain = urlparse(args.url).netloc
    out = Path("output") / domain
    out.mkdir(parents=True, exist_ok=True)

    headless = HEADLESS and not args.show_browser
    print(f"Browser mode: {'hidden (headless)' if headless else 'visible'}")
    pages, all_links = crawl(args.url, out, args.max_pages, args.delay,
                             headless, not args.respect_robots)
    if not pages:
        print("No pages captured.")
        return

    features, tests, prices = analyze(pages)
    (out / "data.json").write_text(json.dumps(
        {"pages": pages, "features": features, "tests": tests, "prices": prices,
         "discovered_urls": all_links}, indent=2, ensure_ascii=False), encoding="utf-8")

    report = build_html(domain, pages, features, tests, prices, out)
    html_to_pdf(report, out / "report.pdf")
    print(f"\nDone! Open: {out / 'report.pdf'}")


if __name__ == "__main__":
    main()