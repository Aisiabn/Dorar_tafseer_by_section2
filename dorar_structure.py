import requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0"})

url = "https://dorar.net/tafseer/1/1"  # أول مقطع في الفاتحة
r = s.get(url, timeout=20)

soup = BeautifulSoup(r.text, "html.parser")

# كم article موجود؟
articles = soup.find_all("article")
print(f"عدد article: {len(articles)}")

# أول 2000 حرف من الـ HTML
print(r.text[:2000])