"""
موسوعة التفسير — dorar.net
المخرج: ملف Markdown منفصل لكل عنوان فرعي (span.title-1)
كل segment يستخرج حواشيه بشكل مستقل — لا مشاركة بين الأقسام
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
TASHKEEL   = re.compile(
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
    return re.sub(r'\s+', ' ', text).strip()

def fuzzy_key(heading, threshold=0.82):
    norm = normalize(heading)
    best_score, best_key = 0.0, None
    for k in _known_keys:
        s = SequenceMatcher(None, norm, k).ratio()
        if s > best_score:
            best_score, best_key = s, k
    if best_score >= threshold:
        return best_key
    _known_keys.append(norm)
    return norm

def safe_filename(text):
    text = TASHKEEL.sub('', text)
    text = re.sub(r'[\\/:*?"<>|]', '', text).strip().rstrip(':').strip()
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
        "Accept"                   : "text/html,application/xhtml+xml,*/*;q=0.8",
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
    soup = BeautifulSoup(html, "html.parser")
    cands = []
    for a in soup.find_all("a", href=SECTION_RE):
        m = SECTION_RE.match(a["href"])
        if m and int(m.group(1)) == surah_num:
            cands.append((int(m.group(2)), BASE + a["href"]))
    if cands:
        cands.sort()
        return cands[0][1]
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
# معالجة nodes مستقلة لكل segment
# ─────────────────────────────────────────────
def process_nodes(nodes):
    """
    يحوّل قائمة nodes مباشرة إلى (text, footnotes).
    الحواشي مرقّمة محلياً من 1 — مستقلة تماماً عن باقي الأقسام.
    """
    fn_counter = 1
    footnotes  = []
    parts      = []

    for node in nodes:
        if isinstance(node, NavigableString):
            parts.append(str(node))
        elif isinstance(node, Tag):
            cls = node.get("class", [])
            if node.name == "br":
                parts.append("\n")
            elif "tip" in cls:
                fn_text = node.get_text(strip=True)
                if fn_text:
                    footnotes.append(f"[^{fn_counter}]: {fn_text}")
                    parts.append(f" [^{fn_counter}]")
                    fn_counter += 1
            elif "aaya" in cls:
                parts.append(f"﴿{node.get_text(strip=True)}﴾")
            elif "hadith" in cls:
                parts.append(f"«{node.get_text(strip=True)}»")
            elif "sora" in cls:
                t = node.get_text(strip=True)
                if t:
                    parts.append(f" {t} ")
            elif "title-1" in cls:
                pass  # لا يجب أن يصل هنا
            else:
                parts.append(node.get_text(" ", strip=False))

    text = "".join(parts)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text, footnotes

# ─────────────────────────────────────────────
# استخراج الأقسام من صفحة
# ─────────────────────────────────────────────
def extract_title1_blocks(html):
    soup   = BeautifulSoup(html, "html.parser")
    blocks = []

    for article in soup.find_all("article", class_="border-bottom"):
        h5 = article.find("h5", class_="default-text-color")
        if not h5 or "modal-title" in h5.get("class", []):
            continue
        l3_heading = h5.get_text(strip=True)

        p = article.find("p")
        if not p:
            continue

        # جمع كل title-1 مباشرة في p
        t1_spans = [
            n for n in p.children
            if isinstance(n, Tag) and "title-1" in n.get("class", [])
        ]
        if not t1_spans:
            continue

        # بناء خريطة: t1_span → الـ nodes التي تليه حتى t1 التالي
        t1_set = set(id(s) for s in t1_spans)

        for idx, t1 in enumerate(t1_spans):
            title_text = t1.get_text(strip=True)
            if not title_text:
                continue

            # اجمع siblings حتى t1 التالي
            seg_nodes = []
            node = t1.next_sibling
            while node is not None:
                if isinstance(node, Tag) and id(node) in t1_set:
                    break
                seg_nodes.append(node)
                node = node.next_sibling

            text, footnotes = process_nodes(seg_nodes)
            if not text:
                continue

            key = fuzzy_key(title_text)
            blocks.append({
                "key"      : key,
                "display"  : title_text,
                "l3"       : l3_heading,
                "text"     : text,
                "footnotes": footnotes,
            })

    return blocks

# ─────────────────────────────────────────────
# إعادة الترقيم العالمي
# ─────────────────────────────────────────────
def renum(text, fns, global_fn):
    """
    الحواشي محلية [^1],[^2],... — نعيد ترقيمها عالمياً.
    كل رقم في النص له بالضرورة تعريف في fns لأنهما أُنشئا معاً.
    """
    local_map = {}
    for fn in fns:
        m = re.match(r'\[\^(\d+)\]:', fn)
        if m:
            orig = m.group(1)
            if orig not in local_map:
                local_map[orig] = str(global_fn)
                global_fn += 1

    for loc, gbl in local_map.items():
        text = re.sub(rf'\[\^{re.escape(loc)}\]', f'[^{gbl}]', text)

    new_fns = []
    for fn in fns:
        m = re.match(r'\[\^(\d+)\]:(.*)', fn, re.DOTALL)
        if m:
            gbl = local_map.get(m.group(1))
            if gbl:
                new_fns.append(f"[^{gbl}]:{m.group(2)}")

    return text, new_fns, global_fn

# ─────────────────────────────────────────────
# قاعدة التجميع
# ─────────────────────────────────────────────
def register(db, block, surah_title, page_title):
    k = block["key"]
    if k not in db:
        db[k] = {"display": block["display"], "entries": []}
    db[k]["entries"].append({
        "surah"      : surah_title,
        "page_title" : page_title,
        "l3"         : block["l3"],
        "text"       : block["text"],
        "footnotes"  : block["footnotes"],
    })

# ─────────────────────────────────────────────
# الزحف
# ─────────────────────────────────────────────
def crawl(session, surah_links):
    db = {}
    for surah in surah_links:
        snum, stitle, surl = surah["num"], surah["title"], surah["url"]
        print(f"\n{'='*55}\n[{snum:3d}] {stitle}")

        html_s = get_page(session, surl, referer=INDEX)
        time.sleep(DELAY)
        if not html_s:
            continue

        for blk in extract_title1_blocks(html_s):
            register(db, blk, stitle, f"تعريف {stitle}")

        first_url = get_first_section_link(html_s, snum)
        if not first_url:
            print("  ⚠ لا مقاطع")
            continue

        next_url, visited = first_url, set()
        while next_url and next_url not in visited:
            visited.add(next_url)
            html_p = get_page(session, next_url, referer=surl)
            time.sleep(DELAY)
            if not html_p:
                break
            ptitle = get_page_title(html_p)
            for blk in extract_title1_blocks(html_p):
                register(db, blk, stitle, ptitle)
                print(f"    {ptitle[:35]:35s}  → {blk['display'][:30]}")
            next_url = get_next_link(html_p)

    return db

# ─────────────────────────────────────────────
# الحفظ
# ─────────────────────────────────────────────
def save_db(db):
    os.makedirs(OUT_DIR, exist_ok=True)

    for key, info in db.items():
        display, entries = info["display"], info["entries"]
        fpath = os.path.join(OUT_DIR, safe_filename(display) + ".md")

        all_footnotes = []
        global_fn     = 1

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
            lines.append(f"*ضمن: {entry['l3']}*\n\n")

            text, new_fns, global_fn = renum(
                entry["text"], entry["footnotes"], global_fn
            )
            if text:
                lines.append(f"{text}\n\n")
            all_footnotes.extend(new_fns)
            lines.append("---\n\n")

        if all_footnotes:
            lines.append("\n## الحواشي\n\n")
            for fn in all_footnotes:
                lines.append(f"{fn}\n")

        with open(fpath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        print(f"  ✔ {safe_filename(display)}.md  ({len(entries)} مقطع، {len(all_footnotes)} حاشية)")

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