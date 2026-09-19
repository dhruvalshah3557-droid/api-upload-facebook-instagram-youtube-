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
    "tl": [
        "✨ {title} — {description}",
        "Tuklasin ang kagandahan ng {title}. ✨ {description}",
        "Iangat ang iyong estilo sa {title}. {description}",
        "Bago sa koleksyon namin: {title}. {description}",
        "Gumawa ng pahayag gamit ang {title}. {description}",
    ],
    "zh": [
        "✨ {title} — {description}",
        "发现 {title} 之美 ✨",
        "用 {title} 提升您的风格",
        "{title} 新品到店！{description}",
        "让 {title} 成为您的点睛之笔",
    ],
    "ru": [
        "✨ {title} — {description}",
        "Откройте для себя красоту {title} ✨",
        "Поднимите свой стиль с {title}",
        "{title} уже в наличии! {description}",
        "Создайте образ с {title}",
    ],
    "ja": [
        "✨ {title} — {description}",
        "{title} の美しさをご覧ください ✨",
        "{title} でスタイルを格上げ",
        "{title} 新入荷！{description}",
        "{title} で印象的なスタイルに",
    ],
    "ko": [
        "✨ {title} — {description}",
        "{title}의 아름다움을 발견하세요 ✨",
        "{title}로 스타일을 업그레이드하세요",
        "{title} 새로 입고되었습니다! {description}",
        "{title}로 특별한 스타일을 연출하세요",
    ],
    "el": [
        "✨ {title} — διαχρονική κομψότητα και μοναδική λάμψη.",
        "Ανακαλύψτε την ομορφιά του {title}. ✨",
        "Αναδείξτε το στυλ σας με το {title}.",
        "Νέα άφιξη: {title}. Ένα κόσμημα που ξεχωρίζει.",
        "Κάντε κάθε στιγμή ξεχωριστή με το {title}.",
    ],
    "tr": [
        "{title} \u2014 zamans\u0131z \u015f\u0131kl\u0131k ve e\u015fsiz bir \u0131\u015f\u0131lt\u0131.",
        "{title} g\u00fczelli\u011fini ke\u015ffedin.",
        "Tarz\u0131n\u0131z\u0131 {title} ile \u00f6ne \u00e7\u0131kar\u0131n.",
        "Yeni gelen: {title}. Fark yaratan bir m\u00fccevher.",
        "Her an\u0131 {title} ile \u00f6zel k\u0131l\u0131n.",
    ],
    "lb": [
        "✨ {title} — أناقة خالدة ولمعان فريد.",
        "اكتشفوا جمال {title}. ✨",
        "خلّوا ستايلكم يبرز مع {title}.",
        "وصل حديثاً: {title}. مجوهرات بتميّز خاص.",
        "خلّوا كل لحظة مميزة مع {title}.",
    ],
    "cs": [
        "✨ {title} — nadčasová elegance a jedinečný lesk.",
        "Objevte krásu {title}. ✨",
        "Podtrhněte svůj styl s {title}.",
        "Novinka: {title}. Šperk, který vynikne.",
        "Udělejte z každého okamžiku něco výjimečného s {title}.",
    ],
}

HASHTAG_TEMPLATES = {
    "en": ["#ColourDiam", "#FineJewelry", "#Luxury", "#Diamond", "#Elegance"],
    "th": ["#ColourDiam", "#เครื่องประดับ", "#แหวนเพชร", "#ของขวัญ", "#สวยเก๋"],
    "my": ["#ColourDiam", "#လက်ဝတ်ရတနာ", "#စိန်", "#ဇိမ်ခံ", "#လက်ဆောင်"],
    "tl": ["#ColourDiam", "#Alahas", "#Diamante", "#Marangya", "#Regalo"],
    "zh": ["#ColourDiam", "#钻石", "#奢华", "#珠宝", "#礼物"],
    "ru": ["#ColourDiam", "#ювелирныеизделия", "#бриллианты", "#роскошь", "#подарок"],
    "ja": ["#ColourDiam", "#ジュエリー", "#ダイヤモンド", "#ラグジュアリー", "#ギフト"],
    "ko": ["#ColourDiam", "#주얼리", "#다이아몬드", "#럭셔리", "#선물"],
    "el": ["#ColourDiam", "#Διαμάντια", "#Κοσμήματα", "#ΠολυτελήΚοσμήματα", "#Κομψότητα"],
    "tr": ["#ColourDiam", "#Elmas", "#Mücevher", "#LüksMücevher", "#Pırlanta"],
    "lb": ["#ColourDiam", "#ألماس", "#مجوهرات", "#مجوهرات_فاخرة", "#لبنان"],
    "cs": ["#ColourDiam", "#Diamanty", "#Šperky", "#LuxusníŠperky", "#Elegantní"],
}

PAGE_LANG_MAP = {
    "colour diam ph": "tl",
    "colour diam philippines": "tl",
    "color diam philippines": "tl",
    "colour diam myanmar": "my",
    "color diam myanmar": "my",
    "colordiamonds": "en",
    "colour diam bangkok": "th",
    "colour diam thailand": "th",
    "colour diam china": "zh",
    "colour diam russia": "ru",
    "colour diam japan": "ja",
    "colour diam japanese": "ja",
    "color diam japan": "ja",
    "color diam japanese": "ja",
    "colour diam korea": "ko",
    "colour diam korean": "ko",
    "color diam korea": "ko",
    "color diam korean": "ko",
    "colour diam taiwan": "zh",
    "colour diam hong kong": "zh",
    "colour diam israel": "he",
    "israel": "he",
    "hebrew": "he",
    "colour diam spain": "es",
    "colour diam greece": "el",
    "colour diam turkey": "tr",
    "colour diam lebanon": "lb",
    "colour diam czech": "cs",
    "colour diam czechia": "cs",
    "colour diam arabic": "ar",
    "trending jewel": "en",
    "colour diam limited": "en",
    "colour diam": "en",
    "nfcd": "en",
}


def get_lang(page_name):
    key = page_name.strip().lower()
    if key in PAGE_LANG_MAP:
        return PAGE_LANG_MAP[key]
    for name in sorted(PAGE_LANG_MAP, key=len, reverse=True):
        if name in key:
            lang = PAGE_LANG_MAP[name]
            return lang
    return "en"


def generate_caption(product_info, page_name="", language=""):
    lang = str(language or "").split("-")[0].lower() or get_lang(page_name)
    title = str(product_info.get("title") or "").strip() or "Diamond Jewelry"
    description = str(product_info.get("description") or "").strip()
    if description == title:
        description = ""
    templates = CAPTION_TEMPLATES.get(lang, CAPTION_TEMPLATES["en"])
    template = random.choice(templates)
    return template.format(title=title[:80], description=description[:100]).strip()


def generate_hashtags(product_info, page_name="", language=""):
    lang = str(language or "").split("-")[0].lower() or get_lang(page_name)
    base_tags = HASHTAG_TEMPLATES.get(lang, HASHTAG_TEMPLATES["en"])
    keywords = product_info.get("keywords", [])
    extra = []
    for kw in keywords[:3]:
        clean = kw.replace(" ", "").replace("-", "")
        if clean:
            extra.append(f"#{clean}")
    return " ".join(base_tags[:4] + extra[:3])
