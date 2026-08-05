import json
import logging
import re

import requests
from bs4 import BeautifulSoup

from config import Config

logger = logging.getLogger(__name__)

PLACEHOLDER_MARKERS = ("ColorDiam.png", "/assets/img/")

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "with", "and", "18k", "gm",
    "ct", "carat", "certified", "natural", "loose",
}


class ColourDiamFetcher:
    BASE_URL = getattr(Config, "COLORDIAM_BASE_URL", "https://www.colourdiam.com")

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
        })

    def _get(self, url):
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return resp

    def _abs(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http"):
            return url
        return self.BASE_URL + url

    def get_featured_products(self):
        html = json.loads(self._get(f"{self.BASE_URL}/Home/FeaturedProduct").text)
        soup = BeautifulSoup(html, "html.parser")
        products = []
        for item in soup.select(".product-item"):
            a = item.select_one("a[href*='/productdetail/']")
            if not a:
                continue
            href = a.get("href", "")
            pid = href.rstrip("/").split("/")[-1]
            if not pid.isdigit():
                continue
            name_el = item.select_one(".product-name")
            price_el = item.select_one(".price-regular")
            products.append({
                "id": pid,
                "url": self._abs(href),
                "title": name_el.get_text(" ", strip=True) if name_el else "",
                "price": price_el.get_text(strip=True) if price_el else "",
            })
        return products

    def get_product_media(self, product_id):
        try:
            resp = self._get(f"{self.BASE_URL}/productdetail/Menu%20/{product_id}")
            return self._parse_detail(resp.text, product_id)
        except Exception as e:
            logger.warning(f"Could not fetch detail for {product_id}: {e}")
            return {"id": product_id, "title": "", "description": "", "keywords": [], "images": [], "video": ""}

    def _parse_detail(self, html, product_id):
        soup = BeautifulSoup(html, "html.parser")

        title = ""
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].split("|")[0].strip()
        if not title:
            h = soup.select_one("h3.product-name")
            title = h.get_text(" ", strip=True) if h else ""

        description = ""
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            description = og_desc["content"].strip()
        if not description:
            description = title

        video = ""
        images = []
        slider = soup.select_one("#imgSlider")
        if slider:
            v = slider.find("video")
            if v and v.get("src"):
                video = self._abs(v["src"].strip())
            for img in slider.find_all("img"):
                src = (img.get("src") or "").strip()
                if src:
                    images.append(self._abs(src))

        return {
            "id": product_id,
            "title": title,
            "description": description,
            "keywords": self._keywords_from_title(title),
            "images": images,
            "video": video,
        }

    @staticmethod
    def _keywords_from_title(title):
        seen = []
        for w in re.split(r"[^A-Za-z0-9]+", title):
            if not w or w.lower() in STOPWORDS:
                continue
            if w.lower() in (s.lower() for s in seen):
                continue
            seen.append(w)
        return seen[:5]

    @staticmethod
    def media_url(product):
        video = (product.get("video") or "").strip()
        if video:
            return video
        for img in product.get("images") or []:
            if img and not any(m in img for m in PLACEHOLDER_MARKERS):
                return img
        return ""

    def enrich(self, featured):
        merged = dict(featured)
        merged.update(self.get_product_media(featured["id"]))
        return merged
