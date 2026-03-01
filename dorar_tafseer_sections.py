"""
موسوعة التفسير — dorar.net
المخرج: ملف Markdown منفصل لكل قسم (L3/h5)
المحتوى: السورة → المقطع → عنوان فرعي (span.title-1) → النص
الحواشي مجمّعة في نهاية كل ملف
"""

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
import re, time, os, traceback
from difflib import SequenceMatcher

BASE    = "https://dorar.net"
INDEX   = "https://dorar.net/tafseer"
DELAY   = 1.2
OUT_DIR = "dorar_tafseer_sections"

_val        = os.environ.get("TEST_SURAHS", "None")
TEST_SURAHS = None if _val == "None" else int(_val)

SURAH_RE   = re.compile(r"^/tafseer/(\d+)$")
SECTION_RE = re.compile(r"^/tafseer/(\d+)/(\d+)$")

TASHKEEL = re.compile(
    r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]'
)

# ─────────────────────────────────────────────
# أدوات مساعدة
# ─────────────────────────────────────────────
_known_keys: list = []

def normalize(text):
    text = TASHKEEL.sub('', text)
    text = re.sub(r'[أإآٱ]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def fuzzy_key(heading: str, threshold: float = 0.82) -> str:
    norm = normalize(heading)
    best_score, best_key = 0.0, None
    for k in _known_keys:
        score = SequenceMatcher(None, norm, k).ratio()
        if score > best_score:
            best_score, best_key = score, k
    if best_score >= threshold:
        return best_key
    _known_keys.append(norm)
    return norm

def safe_filename(text: str) -> str:
    text = TASHKEEL.sub('', text)
    text = re.sub(r'[\\/:*?"<>|]', '', text)
    text = text.strip().rstrip(':').strip()
    return text[:80] or "قسم"

# ─────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────
def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent"               : "Mozilla/5.0 (Windows NT 6.1; WOW64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/109.0.0.0 Safari/537.36",
        "Accept"                   : "text/html,application/xhtml+xml,application/xml;"
                                     "q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language"          : "ar,en-US;q=0.9,en;q=0.8",
        "Connection"               : "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s

def get_page(session, url, referer=INDEX):
    session.headers["Referer"] = referer
    try:
        r = session.get(url, timeout=20)
        print(f"  [{r.status_code}] {url}")
        return r.text if r.status_code == 200 else ""
    except Exception as e:
        print(f"  [ERR] {url} — {e}")
        return ""

# ─────────────────────────────────────────────
# روابط
# ─────────────────────────────────────────────
def get_surah_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links, seen = [], set()
    for card in soup.find_all("div", class_="card-personal"):
        a = card.find("a", href=SURAH_RE)
        if not a:
            continue
        href  = a["href"]
        title = a.get_text(strip=True)
        if href in seen or not title:
            continue
        seen.add(href)
        num = int(SURAH_RE.match(href).group(1))
        links.append({"url": BASE + href, "title": title, "num": num})
    links.sort(key=lambda x: x["num"])
    return links

def get_first_section_link(html, surah_num):
    soup       = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=SECTION_RE):
        m = SECTION_RE.match(a["href"])
        if m and int(m.group(1)) == surah_num:
            candidates.append((int(m.group(2)), BASE + a["href"]))
    if candidates:
        candidates.sort()
        return candidates[0][1]
    return None

def get_next_link(html):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=SECTION_RE):
        if "التالي" in a.get_text():
            return BASE + a["href"]
    return None

def get_page_title(html):
    soup = BeautifulSoup(html, "html.parser")
    og   = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].split(" - ", 1)[-1].strip()
    t = soup.find("title")
    if t:
        return t.get_text().split(" - ")[-1].strip()
    return ""

# ─────────────────────────────────────────────
# استخراج الحواشي من span.tip
# ─────────────────────────────────────────────
TIP_NUM_RE = re.compile(r'^\[(\d+)\]')

def extract_tips(p_tag):
    """
    يستخرج الحواشي من span.tip داخل <p>،
    ويستبدل كل span.tip بـ [^N] في النص.
    يعيد (footnotes: dict, p_tag معدّل نسخياً)
    """
    footnotes = {}
    for span in p_tag.find_all("span", class_="tip"):
        raw = span.get_text(" ", strip=True)
        m   = TIP_NUM_RE.match(raw)
        if m:
            num  = m.group(1)
            body = re.sub(r'\s+', ' ', raw[len(m.group(0)):]).strip()
            footnotes[num] = body
            span.replace_with(f"[^{num}]")
        else:
            span.decompose()
    return footnotes

# ─────────────────────────────────────────────
# تحليل <p> إلى شرائح (subheading + text)
# ─────────────────────────────────────────────
def parse_paragraph(p_tag):
    """
    يقرأ محتوى <p> node بnode،
    ويقسّمه على span.title-1 كعناوين فرعية.
    يعيد قائمة: [{"type": "subheading"|"text", "text": "..."}]
    """
    chunks   = []
    buf      = []

    def flush_buf():
        t = re.sub(r'\s+', ' ', " ".join(buf)).strip()
        if t:
            chunks.append({"type": "text", "text": t})
        buf.clear()

    for node in p_tag.children:
        if isinstance(node, NavigableString):
            s = str(node).strip()
            if s:
                buf.append(s)
        elif isinstance(node, Tag):
            if node.name == "span" and "title-1" in node.get("class", []):
                flush_buf()
                heading = node.get_text(strip=True)
                if heading:
                    chunks.append({"type": "subheading", "text": heading})
            elif node.name == "br":
                buf.append(" ")
            else:
                # أي عنصر آخر (aaya, sora, hadith, ...) نأخذ نصه
                t = node.get_text(" ", strip=True)
                if t:
                    buf.append(t)

    flush_buf()
    return chunks

# ─────────────────────────────────────────────
# استخراج الأقسام من صفحة
# ─────────────────────────────────────────────
def extract_sections(html):
    """
    يعيد قائمة أقسام من صفحة واحدة:
    [
      {
        "l3"       : "تفسير الآيات",
        "l3_key"   : "...",
        "chunks"   : [{"type": "subheading"|"text", "text": "..."}],
        "footnotes": {"26": "يُنظر: ...", ...}
      },
      ...
    ]
    """
    soup     = BeautifulSoup(html, "html.parser")
    sections = []
    seen     = set()

    # كل قسم داخل <article>
    for article in soup.find_all("article", class_="border-bottom"):
        h5 = article.find("h5", class_="default-text-color")
        if not h5:
            continue
        if "modal-title" in h5.get("class", []):
            continue

        heading = h5.get_text(strip=True)
        if not heading:
            continue

        l3_key = fuzzy_key(heading)
        if l3_key in seen:
            continue
        seen.add(l3_key)

        p = article.find("p")
        if not p:
            continue

        # استخراج الحواشي أولاً (يعدّل p في المكان)
        footnotes = extract_tips(p)

        # تقطيع المحتوى
        chunks = parse_paragraph(p)

        sections.append({
            "l3"       : heading,
            "l3_key"   : l3_key,
            "chunks"   : chunks,
            "footnotes": footnotes,
        })

    return sections

# ─────────────────────────────────────────────
# قاعدة التجميع
# ─────────────────────────────────────────────
def build_db():
    return {}

def register(db, sec, surah_title, page_title):
    k = sec["l3_key"]
    if k not in db:
        db[k] = {"display": sec["l3"], "entries": []}
    db[k]["entries"].append({
        "surah"      : surah_title,
        "page_title" : page_title,
        "chunks"     : sec["chunks"],
        "footnotes"  : sec["footnotes"],
    })

# ─────────────────────────────────────────────
# الزحف
# ─────────────────────────────────────────────
def crawl(session, surah_links):
    db = build_db()

    for surah in surah_links:
        snum   = surah["num"]
        stitle = surah["title"]
        surl   = surah["url"]

        print(f"\n{'='*55}")
        print(f"[{snum:3d}] {stitle}")

        html_s = get_page(session, surl, referer=INDEX)
        time.sleep(DELAY)
        if not html_s:
            continue

        for sec in extract_sections(html_s):
            register(db, sec, stitle, f"تعريف {stitle}")

        first_url = get_first_section_link(html_s, snum)
        if not first_url:
            print("  ⚠ لا مقاطع")
            continue

        next_url = first_url
        visited  = set()

        while next_url and next_url not in visited:
            visited.add(next_url)
            html_p = get_page(session, next_url, referer=surl)
            time.sleep(DELAY)
            if not html_p:
                break

            ptitle = get_page_title(html_p)
            secs   = extract_sections(html_p)
            for sec in secs:
                register(db, sec, stitle, ptitle)
                print(f"    {ptitle[:35]:35s}  → {sec['l3'][:30]}")

            next_url = get_next_link(html_p)

    return db

# ─────────────────────────────────────────────
# الحفظ
# ─────────────────────────────────────────────
def render_entry(entry, fn_counter):
    """
    يحوّل entry واحد إلى نص Markdown مع إعادة ترقيم الحواشي.
    يعيد (lines, fn_counter_new, [(glob_num, body), ...])
    """
    fn_map     = {}   # رقم أصلي → رقم عالمي جديد
    collected  = []   # [(glob_num, body)]
    lines      = []

    def remap(m):
        orig = m.group(1)
        if orig not in fn_map:
            nonlocal fn_counter
            fn_counter += 1
            fn_map[orig] = fn_counter
            body = entry["footnotes"].get(orig, "")
            collected.append((fn_counter, body))
        return f"[^{fn_map[orig]}]"

    for chunk in entry["chunks"]:
        if chunk["type"] == "subheading":
            lines.append(f"\n#### {chunk['text']}\n")
        else:
            txt = re.sub(r'\[\^(\d+)\]', remap, chunk["text"])
            txt = re.sub(r'\s+', ' ', txt).strip()
            if txt:
                lines.append(f"\n{txt}\n")

    return lines, fn_counter, collected


def save_db(db):
    os.makedirs(OUT_DIR, exist_ok=True)

    for l3_key, info in db.items():
        display = info["display"]
        entries = info["entries"]
        fname   = safe_filename(display) + ".md"
        fpath   = os.path.join(OUT_DIR, fname)

        all_footnotes = []
        fn_counter    = 0

        lines = [
            f"# {display}\n\n",
            f"> المصدر: موسوعة التفسير — dorar.net  \n",
            f"> عدد المقاطع: {len(entries)}\n\n",
            "---\n\n",
        ]

        current_surah = None
        for entry in entries:
            if entry["surah"] != current_surah:
                current_surah = entry["surah"]
                lines.append(f"\n## سورة {current_surah}\n\n")

            lines.append(f"### {entry['page_title']}\n")

            body_lines, fn_counter, collected = render_entry(entry, fn_counter)
            lines.extend(body_lines)
            all_footnotes.extend(collected)
            lines.append("\n---\n\n")

        if all_footnotes:
            lines.append("\n## الحواشي\n\n")
            for num, body in sorted(all_footnotes, key=lambda x: x[0]):
                lines.append(f"[^{num}]: {body}\n")

        with open(fpath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        print(f"  ✔ {fname}  ({len(entries)} مقطع)")

    print(f"\n✔ {len(db)} ملف في {OUT_DIR}/")

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        session = make_session()

        print("① تهيئة الجلسة...")
        get_page(session, INDEX, referer=BASE)
        time.sleep(1.5)

        print("\n② جلب الصفحة الرئيسية...")
        html_main = get_page(session, INDEX, referer=BASE)
        time.sleep(2)
        if not html_main:
            raise SystemExit("فشل جلب الصفحة الرئيسية")

        surah_links = get_surah_links(html_main)
        print(f"\n③ {len(surah_links)} سورة مكتشفة")

        if TEST_SURAHS:
            surah_links = surah_links[:TEST_SURAHS]

        print("\n④ الزحف واستخراج المحتوى...")
        db = crawl(session, surah_links)

        print("\n⑤ الحفظ...")
        save_db(db)

        print("\n✔ اكتمل.")

    except SystemExit as e:
        print(e)
    except Exception:
        traceback.print_exc()