import logging
import random

logger = logging.getLogger(__name__)

CAPTION_TEMPLATES = {
    "en": [
        "✨ {title} — {description}",
        "Discover the beauty of {title}. ✨ {description}",
        "Elevate your style with {title}. {description}",
        "Stunning {title} just arrived! {description}",
        "Make a statement with {title}. {description}",
    ],
    "th": [
        "✨ {title} — {description}",
        "ค้นพบความงามของ {title} ✨",
        "ยกระดับสไตล์ของคุณด้วย {title}",
        "{title} สุด stunning มาใหม่!",
        "สร้างความโดดเด่นด้วย {title}",
    ],
    "my": [
        "✨ {title} — {description}",
        "{title} လှပမှုကိုရှာဖွေပါ ✨",
        "{title} ဖြင့်သင့်စတိုင်ကိုမြှင့်တင်ပါ",
        "{title} အသစ်ရောက်ရှိပြီ",
        "{title} ဖြင့်ထူးခြားမှုကိုဖန်တီးပါ",
    ],
}

HASHTAG_TEMPLATES = {
    "en": ["#ColourDiam", "#FineJewelry", "#Luxury", "#Diamond", "#Elegance"],
    "th": ["#ColourDiam", "#เครื่องประดับ", "#แหวนเพชร", "#ของขวัญ", "#สวยเก๋"],
    "my": ["#ColourDiam", "#လက်ဝတ်ရတနာ", "#စိန်", "#ဇိမ်ခံ", "#လက်ဆောင်"],
}

PAGE_LANG_MAP = {
    "colour diam ph": "en",
    "colour diam myanmar": "my",
    "colordiamonds": "en",
    "colour diam bangkok": "th",
    "trending jewel": "en",
    "colour diam limited": "en",
    "colour diam": "en",
    "nfcd": "en",
}


def get_lang(page_name):
    key = page_name.strip().lower()
    for name, lang in PAGE_LANG_MAP.items():
        if name in key or key in name:
            return lang
    return "en"


def generate_caption(product_info, page_name=""):
    lang = get_lang(page_name)
    title = product_info.get("title", "Diamond Jewelry")
    description = product_info.get("description", "Discover timeless elegance")
    templates = CAPTION_TEMPLATES.get(lang, CAPTION_TEMPLATES["en"])
    template = random.choice(templates)
    return template.format(title=title[:50], description=description[:100])


def generate_hashtags(product_info, page_name=""):
    lang = get_lang(page_name)
    base_tags = HASHTAG_TEMPLATES.get(lang, HASHTAG_TEMPLATES["en"])
    keywords = product_info.get("keywords", [])
    extra = []
    for kw in keywords[:3]:
        clean = kw.replace(" ", "").replace("-", "")
        if clean:
            extra.append(f"#{clean}")
    return " ".join(base_tags[:4] + extra[:3])
