import requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0"})

url = "https://dorar.net/tafseer/1/1"
r = s.get(url, timeout=20)

soup = BeautifulSoup(r.text, "html.parser")

articles = soup.find_all("article")
print(f"عدد article: {len(articles)}")

# ابحث عن h3,h4,h5 في الصفحة
for tag in soup.find_all(["h3","h4","h5"])[:20]:
    print(f"<{tag.name} class='{tag.get('class','')}'>: {tag.get_text(strip=True)[:80]}")

# ابحث عن span.title-2
spans = soup.find_all("span", class_="title-2")
print(f"\nعدد span.title-2: {len(spans)}")
for sp in spans[:10]:
    print(" -", sp.get_text(strip=True)[:80])

print("\n--- أول 3000 حرف ---")
print(r.text[:3000])