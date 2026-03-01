"""
موسوعة التفسير — dorar.net
المخرج: ملف Markdown منفصل لكل قسم (L3)
المحتوى منظم: السورة → المقطع → عنوان فرعي (title-1) → النص
الحواشي مجمّعة في نهاية كل ملف
"""

import requests
from bs4 import BeautifulSoup
import re, time, os, json, traceback
from difflib import SequenceMatcher

BASE    = "https://dorar.net"
INDEX   = "https://dorar.net/tafseer"
DELAY   = 1.2
OUT_DIR = "dorar_structure"

_val        = os.environ.get("TEST_SURAHS", "None")
TEST_SURAHS = 1 if _val == "None" else int(_val)

SURAH_RE   = re.compile(r"^/tafseer/(\d+)$")
SECTION_RE = re.compile(r"^/tafseer/(\d+)/(\d+)$")

TASHKEEL = re.compile(
    r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]'
)

# ─────────────────────────────────────────────
# مفاتيح التجميع الذكي
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
        parts = og["content"].split(" - ", 1)
        return parts[-1].strip()
    t = soup.find("title")
    if t:
        return t.get_text().split(" - ")[-1].strip()
    return ""

# ─────────────────────────────────────────────
# استخراج المحتوى
# ─────────────────────────────────────────────
NOTE_RE = re.compile(
    r'\[(\d+)\]\s*'
    r'((?:يُنظَر|يُنظر|انظر|ينظر|راجع|أخرجه|رواه)[^\[]*(?:\[[^\]]*\][^\[]*)*)',
    re.UNICODE
)

def extract_footnotes_from_tips(soup_elem):
    """استخراج الحواشي من span.tip"""
    notes = {}
    for span in soup_elem.find_all("span", class_="tip"):
        raw = span.get_text(" ", strip=True)
        m   = NOTE_RE.search(raw)
        if m:
            num  = m.group(1)
            body = re.sub(r'\s+', ' ', m.group(2)).strip()
            notes[num] = body
        span.replace_with(f" [{span.get_text(strip=True).split(']')[0].lstrip('[').strip()}]^fn ")
    return notes

def clean_text(elem) -> str:
    """تنظيف النص من عناصر الواجهة"""
    for tag in elem.find_all(["script", "style", "nav", "footer", "button"]):
        tag.decompose()
    # إزالة روابط المصدر الخارجية (fa-external-link)
    for a in elem.find_all("a"):
        if a.find("i", class_="fa-external-link"):
            a.decompose()
    text = elem.get_text(" ", strip=True)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_sections(html):
    """
    يعيد قائمة من الأقسام:
    [
      {
        "l3"      : "تفسير الآيات",
        "l3_key"  : "...",
        "content" : [
          {"type": "heading", "text": "مناسبة الآية لما قبلها"},
          {"type": "text",    "text": "..."},
          ...
        ],
        "footnotes": {"1": "...", "2": "..."}
      },
      ...
    ]
    """
    soup     = BeautifulSoup(html, "html.parser")
    sections = []
    seen_h5  = set()

    all_h5 = [
        h for h in soup.find_all("h5")
        if "default-text-color" in h.get("class", [])
        and "modal-title" not in h.get("class", [])
        and not any(c in h.get("class", []) for c in ["th5-responsive", "ext-uppercase"])
    ]

    for idx, h5 in enumerate(all_h5):
        heading = h5.get_text(strip=True)
        if not heading:
            continue
        l3_key = fuzzy_key(heading)
        if l3_key in seen_h5:
            continue
        seen_h5.add(l3_key)

        # جمع siblings حتى h5 التالي
        sibs = []
        for sib in h5.find_next_siblings():
            if sib.name == "h5" and "default-text-color" in sib.get("class", []):
                break
            sibs.append(sib)

        # بناء عنصر مؤقت لاستخراج الحواشي
        from bs4 import Tag
        wrapper = BeautifulSoup("", "html.parser")
        container = wrapper.new_tag("div")
        for s in sibs:
            container.append(s.__copy__())
        wrapper.append(container)

        footnotes = extract_footnotes_from_tips(container)

        # تقطيع المحتوى على span.title-1
        content = []
        current_title = None
        buf = []

        def flush(title, buf):
            txt = " ".join(buf).strip()
            txt = re.sub(r'\s+', ' ', txt)
            if txt:
                content.append({"type": "heading" if title else "text",
                                 "text": title or txt,
                                 "body": txt if title else None})
            elif title:
                content.append({"type": "heading", "text": title, "body": ""})

        for sib in container.children:
            if not hasattr(sib, "find_all"):
                t = str(sib).strip()
                if t:
                    buf.append(t)
                continue

            # ابحث عن title-1 داخل العنصر
            spans = sib.find_all("span", class_="title-1")
            if spans:
                # قد يكون في نفس العنصر نص قبل الـ span وبعده
                for span in spans:
                    # النص قبل الـ span
                    before = span.find_previous_sibling(string=True)
                    if current_title is None and buf:
                        txt = clean_text(sib).split(span.get_text(strip=True))[0].strip()
                        if txt:
                            buf.append(txt)
                    # احفظ الـ buffer السابق
                    if current_title is not None or buf:
                        b = " ".join(buf).strip()
                        if current_title:
                            content.append({"type": "subheading", "text": current_title})
                        if b:
                            content.append({"type": "text", "text": b})
                        buf = []
                    current_title = span.get_text(strip=True)
            else:
                txt = clean_text(sib)
                if txt:
                    buf.append(txt)

        # الـ buffer المتبقي
        if current_title:
            content.append({"type": "subheading", "text": current_title})
        if buf:
            content.append({"type": "text", "text": " ".join(buf).strip()})

        sections.append({
            "l3"       : heading,
            "l3_key"   : l3_key,
            "content"  : content,
            "footnotes": footnotes,
        })

    return sections

# ─────────────────────────────────────────────
# هيكل التجميع
# sections_db[l3_key] = {
#   "display" : "تفسير الآيات",
#   "entries" : [
#     {"surah": "الفاتحة", "page_title": "...", "content": [...], "footnotes": {...}},
#     ...
#   ]
# }
# ─────────────────────────────────────────────
def build_db():
    return {}

def register(db, l3_key, display, surah_title, page_title, content, footnotes):
    if l3_key not in db:
        db[l3_key] = {"display": display, "entries": []}
    db[l3_key]["entries"].append({
        "surah"      : surah_title,
        "page_title" : page_title,
        "content"    : content,
        "footnotes"  : footnotes,
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

        html_surah = get_page(session, surl, referer=INDEX)
        time.sleep(DELAY)
        if not html_surah:
            continue

        # صفحة السورة الرئيسية
        for sec in extract_sections(html_surah):
            register(db, sec["l3_key"], sec["l3"],
                     stitle, f"تعريف {stitle}",
                     sec["content"], sec["footnotes"])

        first_url = get_first_section_link(html_surah, snum)
        if not first_url:
            print("  ⚠ لا مقاطع")
            continue

        next_url = first_url
        visited  = set()

        while next_url and next_url not in visited:
            visited.add(next_url)
            html_sec = get_page(session, next_url, referer=surl)
            time.sleep(DELAY)
            if not html_sec:
                break

            page_title = get_page_title(html_sec)
            secs       = extract_sections(html_sec)
            for sec in secs:
                register(db, sec["l3_key"], sec["l3"],
                         stitle, page_title,
                         sec["content"], sec["footnotes"])
                print(f"    {page_title[:35]:35s}  → {sec['l3'][:30]}")

            next_url = get_next_link(html_sec)

    return db

# ─────────────────────────────────────────────
# الحفظ
# ─────────────────────────────────────────────
def render_content(content, fn_offset=0):
    """
    يحوّل قائمة content إلى نص Markdown.
    fn_offset: لتجنب تكرار أرقام الحواشي عبر المقاطع
    يعيد (text, fn_offset_new)
    """
    lines = []
    fn_map   = {}   # رقم أصلي → رقم جديد عالمي
    fn_counter = fn_offset

    def remap(match):
        nonlocal fn_counter
        orig = match.group(1)
        if orig not in fn_map:
            fn_counter += 1
            fn_map[orig] = fn_counter
        return f"[^{fn_map[orig]}]"

    for item in content:
        if item["type"] == "subheading":
            lines.append(f"\n#### {item['text']}\n")
        elif item["type"] == "text":
            txt = re.sub(r'\[(\d+)\]\^fn', remap, item["text"])
            lines.append(f"\n{txt}\n")

    return "\n".join(lines), fn_counter, fn_map

def save_db(db):
    os.makedirs(OUT_DIR, exist_ok=True)

    for l3_key, info in db.items():
        display  = info["display"]
        entries  = info["entries"]
        fname    = safe_filename(display) + ".md"
        fpath    = os.path.join(OUT_DIR, fname)

        all_footnotes = []   # [(num_global, body)]
        fn_offset     = 0
        lines         = [
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

            body, fn_offset, fn_map = render_content(entry["content"], fn_offset)
            lines.append(body)
            lines.append("\n")

            # اجمع الحواشي مع إعادة الترقيم
            rev_map = {v: k for k, v in fn_map.items()}
            for glob_num in sorted(rev_map.keys()):
                orig_num = rev_map[glob_num]
                if orig_num in entry["footnotes"]:
                    all_footnotes.append((glob_num, entry["footnotes"][orig_num]))

            lines.append("---\n\n")

        # الحواشي في النهاية
        if all_footnotes:
            lines.append("\n## الحواشي\n\n")
            for num, body in sorted(all_footnotes):
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