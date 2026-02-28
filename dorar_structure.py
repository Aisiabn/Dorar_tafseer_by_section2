"""
يستخرج هيكل المستويات فقط من موسوعة التفسير — بدون محتوى
المستويات:
  L1 : السورة           /tafseer/{n}
  L2 : المقطع           /tafseer/{n}/{m}
  L3 : article > h5/h4  (العنوان الرئيسي للقسم)
  L4 : span.title-2     (العنوان الفرعي داخل القسم)
"""

import requests
from bs4 import BeautifulSoup
import re
import time
import os
import json
import traceback
from collections import defaultdict
from difflib import SequenceMatcher

BASE    = "https://dorar.net"
INDEX   = "https://dorar.net/tafseer"
DELAY   = 1.2
OUT_DIR = "dorar_structure"

TEST_SURAHS = None  # None = كل القرآن


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
# أنماط الروابط
# ─────────────────────────────────────────────

SURAH_RE   = re.compile(r"^/tafseer/(\d+)$")
SECTION_RE = re.compile(r"^/tafseer/(\d+)/(\d+)$")

# ─────────────────────────────────────────────
# تطبيع + تجميع ذكي
# ─────────────────────────────────────────────

TASHKEEL = re.compile(
    r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]'
)

def normalize(text):
    text = TASHKEEL.sub('', text)
    text = re.sub(r'[أإآٱ]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

_known_keys: list = []

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


# ─────────────────────────────────────────────
# روابط السور
# ─────────────────────────────────────────────

def get_surah_links(html):
    soup  = BeautifulSoup(html, "html.parser")
    links = []
    seen  = set()
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
    for a in soup.find_all("a", href=SECTION_RE):
        if "التالي" in a.get_text():
            return BASE + a["href"]
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
        parts = t.get_text().split(" - ")
        return parts[-1].strip()
    return ""


# ─────────────────────────────────────────────
# استخراج الهيكل فقط
# ─────────────────────────────────────────────

def extract_structure(html):
    """
    يُرجع قائمة من:
      { "l3": عنوان article, "l4": [عناوين title-2 داخله] }
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "form"]):
        tag.decompose()
    for pat in [
        re.compile(r"\bmodal\b"),
        re.compile(r"\breadMore\b"),
        re.compile(r"\balert-dorar\b"),
        re.compile(r"\bcard-personal\b"),
        re.compile(r"\bdefault-gradient\b"),
        re.compile(r"\bfooter-copyright\b"),
    ]:
        for tag in soup.find_all(True, class_=pat):
            tag.decompose()

    results = []
    for article in soup.find_all("article"):
        h_tag   = article.find(["h5", "h4", "h3"])
        heading = h_tag.get_text(strip=True) if h_tag else ""
        if not heading:
            continue

        # L4: العناوين الفرعية داخل القسم
        sub_headings = []
        for span in article.find_all("span", class_="title-2"):
            t = span.get_text(strip=True)
            if t:
                sub_headings.append(t)

        results.append({
            "l3": heading,
            "l4": sub_headings,
        })

    return results


# ─────────────────────────────────────────────
# الزحف
# ─────────────────────────────────────────────

def crawl_structure(session, surah_links):
    """
    يُرجع:
      tree  : { surah_title: [ { "url", "page_title", "sections": [...] } ] }
      l3_db : { fuzzy_key: { "display", "count", "l4_keys": {fuzzy_key: {"display","count"}} } }
    """
    tree  = {}
    l3_db = {}

    for surah in surah_links:
        snum   = surah["num"]
        stitle = surah["title"]
        surl   = surah["url"]

        print(f"\n{'='*55}")
        print(f"[{snum:3d}] {stitle}")

        tree[stitle] = []

        html_surah = get_page(session, surl, referer=INDEX)
        time.sleep(DELAY)
        if not html_surah:
            continue

        # تعريف السورة (L2 = صفحة السورة ذاتها)
        secs = extract_structure(html_surah)
        _register(secs, l3_db)
        tree[stitle].append({"url": surl, "page_title": f"تعريف {stitle}", "sections": secs})

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
            secs       = extract_structure(html_sec)
            _register(secs, l3_db)
            tree[stitle].append({"url": next_url, "page_title": page_title, "sections": secs})

            l3_names = " | ".join(s["l3"][:20] for s in secs)
            print(f"    {page_title[:40]:40s}  L3: {l3_names[:60]}")

            next_url = get_next_link(html_sec)

    return tree, l3_db


def _register(sections, l3_db):
    for sec in sections:
        k3 = fuzzy_key(sec["l3"])
        if k3 not in l3_db:
            l3_db[k3] = {"display": sec["l3"], "count": 0, "l4": {}}
        l3_db[k3]["count"] += 1
        for sub in sec["l4"]:
            k4 = fuzzy_key(sub)
            if k4 not in l3_db[k3]["l4"]:
                l3_db[k3]["l4"][k4] = {"display": sub, "count": 0}
            l3_db[k3]["l4"][k4]["count"] += 1


# ─────────────────────────────────────────────
# الحفظ
# ─────────────────────────────────────────────

def save_results(tree, l3_db):
    os.makedirs(OUT_DIR, exist_ok=True)

    # ── 1. ملف JSON كامل للمستويات ──
    with open(os.path.join(OUT_DIR, "structure.json"), "w", encoding="utf-8") as f:
        json.dump({"l3_db": l3_db, "tree": tree}, f, ensure_ascii=False, indent=2)

    # ── 2. تقرير Markdown مقروء ──
    lines = [
        "# هيكل مستويات موسوعة التفسير\n\n",
        f"> L3 = {len(l3_db)} قسم مختلف\n\n",
        "---\n\n",
    ]

    for k3, info in sorted(l3_db.items(), key=lambda x: -x[1]["count"]):
        lines.append(f"## {info['display']}  *(×{info['count']})*\n\n")
        if info["l4"]:
            for k4, sub in sorted(info["l4"].items(), key=lambda x: -x[1]["count"]):
                lines.append(f"- {sub['display']}  *(×{sub['count']})*\n")
            lines.append("\n")
        lines.append("---\n\n")

    with open(os.path.join(OUT_DIR, "structure.md"), "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"\n✔ {OUT_DIR}/structure.md")
    print(f"✔ {OUT_DIR}/structure.json")
    print(f"\n{'─'*55}")
    print(f"  L3 أقسام فريدة : {len(l3_db)}")
    total_l4 = sum(len(v['l4']) for v in l3_db.values())
    print(f"  L4 أقسام فريدة : {total_l4}")


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
            print(f"   وضع الاختبار: أول {TEST_SURAHS} سور — غيّر TEST_SURAHS = None للكل\n")

        print("\n④ الزحف واستخراج الهيكل...")
        tree, l3_db = crawl_structure(session, surah_links)

        print("\n⑤ الحفظ...")
        save_results(tree, l3_db)

        print("\n✔ اكتمل.")

    except SystemExit as e:
        print(e)
    except Exception:
        traceback.print_exc()