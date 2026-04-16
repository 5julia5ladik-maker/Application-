import base64
import hashlib
import html
import imghdr
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from requests.adapters import HTTPAdapter
from fastapi import Body, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = BASE_DIR / "index.html"
EDITOR_FILE = BASE_DIR / "editor.html"
MANIFEST_FILE = BASE_DIR / "manifest.webmanifest"
SERVICE_WORKER_FILE = BASE_DIR / "sw.js"
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"
IMAGE_CACHE_FILE = DATA_DIR / "image_cache.json"
KEY_FILE = BASE_DIR / "gemini_api_key.txt"
POLLINATIONS_KEY_FILE = BASE_DIR / "pollinations_api_key.txt"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_RECOGNITION_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_RECOGNITION_MODELS",
        f"{GEMINI_MODEL},gemini-2.0-flash,gemini-1.5-flash",
    ).split(",")
    if model.strip()
]
GEMINI_API_VERSIONS = [
    version.strip()
    for version in os.getenv("GEMINI_API_VERSIONS", "v1beta,v1").split(",")
    if version.strip()
]
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_IMAGE_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_IMAGE_MODELS",
        "gemini-2.5-flash-image,gemini-3.1-flash-image-preview,gemini-3-pro-image-preview",
    ).split(",")
    if model.strip()
]
ALLOW_IMAGE_FALLBACK = os.getenv("ALLOW_IMAGE_FALLBACK", "").strip() == "1"
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "pollinations").strip().lower()
ALLOW_APPROX_TEXT_IMAGE = os.getenv("ALLOW_APPROX_TEXT_IMAGE", "1").strip() == "1"
POLLINATIONS_IMAGE_MODEL = os.getenv("POLLINATIONS_IMAGE_MODEL", "flux").strip()
POLLINATIONS_REFERENCE_MODEL = os.getenv(
    "POLLINATIONS_REFERENCE_MODEL", "gptimage-large"
).strip()
POLLINATIONS_IMAGE_MODELS = [
    model.strip()
    for model in os.getenv(
        "POLLINATIONS_IMAGE_MODELS", "flux,seedream,kontext,nanobanana"
    ).split(",")
    if model.strip()
]
IMAGE_WIDTH = int(os.getenv("IMAGE_WIDTH", "720"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "520"))
POLLINATIONS_TIMEOUT = int(os.getenv("POLLINATIONS_TIMEOUT", "75"))
IMAGE_CACHE_MAX_ITEMS = int(os.getenv("IMAGE_CACHE_MAX_ITEMS", "80"))


def load_api_key() -> str:
    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key

    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    return ""


def load_pollinations_api_key() -> str:
    env_key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    if env_key:
        return env_key

    if POLLINATIONS_KEY_FILE.exists():
        try:
            return POLLINATIONS_KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    return ""


GEMINI_API_KEY = load_api_key()
POLLINATIONS_API_KEY = load_pollinations_api_key()

HTTP = requests.Session()
HTTP.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=2,
            connect=2,
            read=1,
            backoff_factor=0.45,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
    ),
)

app = FastAPI(title="HomeStock Universal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROMPT = """
Ты анализируешь фото бытового товара или продукта.
Верни только JSON-объект без markdown и текста вокруг.

Нужные поля:
- product: краткое название товара на русском
- brand: бренд или "Без бренда"
- place: где обычно хранится дома (например: "Ванная", "Кухня", "Дом")
- extra: краткое описание
- total: примерное количество единиц в упаковке, если видно; иначе 1
- usage_rate_guess: примерный расход в единицах в день для обычного дома из 2 человек

Правила:
- Если это одноразка / вейп / vape / e-cig, так и напиши
- Если бренд виден, верни его точно
- Если не уверен, всё равно дай лучший разумный вариант
- total должно быть числом
- usage_rate_guess должно быть числом от 0.01 до 3
""".strip()


def detect_mime(data: bytes) -> str:
    kind = imghdr.what(None, data)
    if kind == "png":
        return "image/png"
    if kind == "gif":
        return "image/gif"
    if kind == "webp":
        return "image/webp"
    return "image/jpeg"


def image_data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('utf-8')}"


def stable_int_seed(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % 2147483647


def load_image_cache() -> dict:
    if not IMAGE_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(IMAGE_CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_image_cache(cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if len(cache) > IMAGE_CACHE_MAX_ITEMS:
        ordered = sorted(
            cache.items(),
            key=lambda item: item[1].get("ts", 0) if isinstance(item[1], dict) else 0,
            reverse=True,
        )
        cache = dict(ordered[:IMAGE_CACHE_MAX_ITEMS])
    IMAGE_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8"
    )


def image_cache_key(model: str, title: str, brand: str, category: str, prompt: str) -> str:
    raw = json.dumps(
        {
            "provider": "pollinations",
            "model": model,
            "title": title,
            "brand": brand,
            "category": category,
            "prompt": prompt,
            "w": IMAGE_WIDTH,
            "h": IMAGE_HEIGHT,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_product_card_svg(
    title: str, brand: str, category: str, original_image: str | None = None
) -> str:
    safe_title = html.escape((title or "Product").strip()[:48])
    safe_brand = html.escape((brand or "HomeStock").strip()[:36])
    safe_category = html.escape((category or "Home").strip()[:28])
    seed = sum(ord(ch) for ch in f"{safe_title}{safe_brand}")
    palettes = [
        ("#1f1711", "#df8a45", "#f8efe3"),
        ("#12332b", "#77b28c", "#f4f0df"),
        ("#2b2436", "#c9a6ff", "#f4efff"),
        ("#2b2118", "#d2a679", "#fff3e2"),
        ("#162536", "#7db7d8", "#eef8ff"),
    ]
    ink, accent, paper = palettes[seed % len(palettes)]
    initials_source = (safe_brand or safe_title or "HS").replace("&amp;", "&")
    initials = html.escape(initials_source[:2].upper())
    image_markup = (
        f'<image href="{html.escape(original_image, quote=True)}" x="240" y="82" '
        'width="420" height="420" preserveAspectRatio="xMidYMid meet"/>'
        if original_image
        else f'<circle cx="450" cy="250" r="94" fill="{ink}"/>'
        f'<text x="450" y="282" text-anchor="middle" font-family="Arial, sans-serif" '
        f'font-size="76" font-weight="800" fill="#fff">{initials}</text>'
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 620">
  <defs>
    <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0" stop-color="{paper}"/>
      <stop offset=".58" stop-color="#ffffff"/>
      <stop offset="1" stop-color="{accent}" stop-opacity=".32"/>
    </linearGradient>
    <radialGradient id="glow" cx=".78" cy=".18" r=".62">
      <stop stop-color="{accent}" stop-opacity=".55"/>
      <stop offset="1" stop-color="{accent}" stop-opacity="0"/>
    </radialGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="24" stdDeviation="24" flood-color="{ink}" flood-opacity=".20"/>
    </filter>
  </defs>
  <rect width="900" height="620" rx="64" fill="url(#bg)"/>
  <rect width="900" height="620" rx="64" fill="url(#glow)"/>
  <circle cx="760" cy="80" r="180" fill="{accent}" opacity=".18"/>
  <circle cx="120" cy="560" r="220" fill="#fff" opacity=".58"/>
  <g filter="url(#shadow)">
    <rect x="256" y="98" width="388" height="424" rx="54" fill="#fff" opacity=".88"/>
    <rect x="294" y="138" width="312" height="344" rx="42" fill="{paper}" opacity=".88"/>
    {image_markup}
  </g>
  <text x="72" y="92" font-family="Arial, sans-serif" font-size="24" font-weight="800" letter-spacing="8" fill="{ink}" opacity=".50">{safe_category}</text>
  <text x="72" y="484" font-family="Arial, sans-serif" font-size="42" font-weight="800" fill="{ink}">{safe_brand}</text>
  <text x="72" y="538" font-family="Arial, sans-serif" font-size="54" font-weight="900" fill="{ink}">{safe_title}</text>
</svg>"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    return f"data:image/svg+xml;base64,{encoded}"


IMAGE_PROMPT = """
Create a new e-commerce product hero image from the provided camera photo.

Critical rules:
- Keep the exact same product identity, size, shape, color, packaging design, labels, logos, and proportions.
- Do not invent a different product.
- Do not rewrite, translate, remove, or add text on the product package.
- Do not crop important parts of the product.
- You may change only the background, lighting, shadows, reflections, composition, and commercial presentation.
- Make the result look like a premium marketplace / online store product card.
- Use a clean studio background, soft natural shadows, high-end lighting, and centered composition.
- Return an image only if possible.
""".strip()


def extract_inline_image(data: dict) -> tuple[str, str] | None:
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return None

    for candidate in candidates:
        parts = (((candidate or {}).get("content") or {}).get("parts")) or []
        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline_data, dict):
                continue
            image_b64 = inline_data.get("data")
            mime = (
                inline_data.get("mimeType")
                or inline_data.get("mime_type")
                or "image/png"
            )
            if image_b64 and str(mime).startswith("image/"):
                return str(image_b64), str(mime)

    return None


def generate_gemini_product_image_with_model(
    raw: bytes, mime: str, title: str, brand: str, category: str, model: str
) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key is missing")

    context = (
        f"\nProduct title: {title or 'unknown'}"
        f"\nBrand: {brand or 'unknown'}"
        f"\nCategory/place: {category or 'unknown'}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": IMAGE_PROMPT + context},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(raw).decode("utf-8"),
                        }
                    },
                ]
            }
        ],
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )

    response = HTTP.post(
        url,
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(stringify_error(data))

    image = extract_inline_image(data)
    if not image:
        raise RuntimeError("Gemini did not return a generated image")

    image_b64, generated_mime = image
    return f"data:{generated_mime};base64,{image_b64}"


def generate_gemini_product_image(
    raw: bytes, mime: str, title: str, brand: str, category: str
) -> tuple[str, str]:
    errors = []
    for model in GEMINI_IMAGE_MODELS:
        try:
            return (
                generate_gemini_product_image_with_model(
                    raw=raw,
                    mime=mime,
                    title=title,
                    brand=brand,
                    category=category,
                    model=model,
                ),
                model,
            )
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            errors.append(f"{model}: {exc}")

    raise RuntimeError(" | ".join(errors) or "Gemini image generation failed")


def build_pollinations_prompt(title: str, brand: str, category: str, prompt_hint: str = "") -> str:
    title = (title or "household product").strip()
    brand = (brand or "").strip()
    category = (category or "home essentials").strip()
    product_name = f"{brand} {title}".strip()
    text = f"{title} {brand} {category}".lower()
    brand_rule = (
        f"The product must look like {brand} packaging and keep the brand identity. "
        if brand and brand.lower() not in {"без бренда", "no brand", "unknown"}
        else "Use neutral realistic packaging because the brand is unknown. "
    )
    packaging_hint = "Use the correct real-world packaging form for this product type."
    if any(word in text for word in ["toilet paper", "paper towel", "tissue", "napkin", "бумага", "салфет", "полотен"]):
        packaging_hint = "Show a soft paper pack or roll bundle, not a bottle."
    elif any(word in text for word in ["spray", "body mist", "perfume", "deodorant", "спрей", "духи", "дезодоран"]):
        packaging_hint = "Show a realistic spray bottle with cap/nozzle, not a jar or box."
    elif any(word in text for word in ["soap", "shampoo", "conditioner", "gel", "lotion", "cream", "мыло", "шампун", "гель", "крем"]):
        packaging_hint = "Show the correct bathroom-care package: bottle, pump, tube, bar, or jar matching the product."
    elif any(word in text for word in ["detergent", "cleaner", "bleach", "dish", "laundry", "уборк", "чист", "моющ", "порошок", "средство"]):
        packaging_hint = "Show household-cleaning packaging such as a detergent bottle, spray trigger, pouch, box, or tub."
    elif any(word in text for word in ["food", "rice", "pasta", "coffee", "tea", "sugar", "snack", "еда", "рис", "макарон", "кофе", "чай", "сахар"]):
        packaging_hint = "Show grocery packaging such as a bag, box, jar, bottle, can, or pouch. Make it look like food packaging."
    elif any(word in text for word in ["battery", "batteries", "charger", "cable", "bulb", "filter", "техника", "батар", "заряд", "кабель", "ламп", "фильтр"]):
        packaging_hint = "Show the correct small household/electronics item or retail box, not a cosmetic bottle."
    return (
        f"Create a realistic standalone ecommerce packshot photo for a home inventory app. "
        f"Product to depict: {product_name}. Product category/context: {category}. "
        f"{brand_rule}{packaging_hint} "
        "The output must be a single clear product photo, not an advertisement poster. "
        "Show exactly one main product or one retail multipack if the product is normally sold as a multipack. "
        "The product must be fully visible, centered, sharp, and front-facing or slight 3/4 angle. "
        "Use a plain white, off-white, or very light neutral studio background with soft natural shadow. "
        "Use realistic materials, realistic size proportions, clean catalog photography, high detail, and no stylized illustration. "
        "If the exact label is unknown, use simple believable label areas and minimal readable text; avoid nonsense lettering. "
        "Strict negatives: no people, no hands, no lifestyle scene, no shelf, no kitchen table clutter, no extra props, no decorative frame, no card UI, no poster layout, no banner, no watermark, no logo overlay, no floating text, no wrong product category, no random bottle when the item is not a bottle. "
        + (f"User correction / extra visual instruction: {html.escape(prompt_hint.strip()[:240])}." if prompt_hint else "")
    )


def build_pollinations_edit_prompt(title: str, brand: str, category: str) -> str:
    title = (title or "product").strip()
    brand = (brand or "").strip()
    category = (category or "home product").strip()
    product_name = f"{brand} {title}".strip()
    return (
        f"Use the reference photo as the source of truth. Create a new realistic "
        f"ecommerce product photograph of the exact same product: {product_name}, "
        f"category {category}. Preserve the same bottle/package shape, color, label, "
        "logo placement, cap, proportions, and visible product details. Do not replace "
        "it with a generic product. Change only background, lighting, shadows, camera "
        "composition, and commercial styling. Plain clean studio setting, full product "
        "visible, sharp focus, no people, no hands, no extra text, no watermark."
    )


def generate_pollinations_reference_image(
    raw: bytes, mime: str, title: str, brand: str, category: str
) -> tuple[str, str]:
    if not POLLINATIONS_API_KEY:
        raise RuntimeError(
            "Pollinations GPT Image reference edit requires POLLINATIONS_API_KEY"
        )

    prompt = quote_plus(build_pollinations_edit_prompt(title, brand, category))
    reference = quote_plus(image_data_url(raw, mime))
    seed = stable_int_seed(f"ref|{title}|{brand}|{category}")
    url = (
        f"https://gen.pollinations.ai/image/{prompt}"
        f"?model={quote_plus(POLLINATIONS_REFERENCE_MODEL)}&width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}&seed={seed}"
        f"&nologo=true&private=true&enhance=true"
        f"&image%5B%5D={reference}"
        f"&key={quote_plus(POLLINATIONS_API_KEY)}"
    )
    response = HTTP.get(url, timeout=max(POLLINATIONS_TIMEOUT, 120))
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Pollinations GPT Image requires a valid Pollinations API key with access to {POLLINATIONS_REFERENCE_MODEL}."
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Pollinations GPT Image failed: HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/"):
        raise RuntimeError("Pollinations GPT Image did not return an image")

    return image_data_url(response.content, content_type), POLLINATIONS_REFERENCE_MODEL


def generate_pollinations_product_image_with_model(
    title: str, brand: str, category: str, model: str, prompt_hint: str = ""
) -> str:
    prompt_text = build_pollinations_prompt(title, brand, category, prompt_hint)
    cache_key = image_cache_key(model, title, brand, category, prompt_text)
    cache = load_image_cache()
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("image"):
        cached["ts"] = time.time()
        cache[cache_key] = cached
        save_image_cache(cache)
        return cached["image"]

    prompt = quote_plus(prompt_text)
    seed = stable_int_seed(f"{title}|{brand}|{category}|{model}")
    url = (
        f"https://image.pollinations.ai/prompt/{prompt}"
        f"?width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}&seed={seed}&nologo=true&private=true"
        f"&enhance=true&model={quote_plus(model)}"
    )
    response = HTTP.get(url, timeout=POLLINATIONS_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"{model}: HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/"):
        raise RuntimeError(f"{model}: did not return an image")

    image = image_data_url(response.content, content_type)
    cache[cache_key] = {
        "image": image,
        "model": model,
        "title": title,
        "brand": brand,
        "category": category,
        "ts": time.time(),
    }
    save_image_cache(cache)
    return image


def generate_pollinations_product_image(
    title: str, brand: str, category: str, prompt_hint: str = ""
) -> tuple[str, str]:
    errors = []
    for model in POLLINATIONS_IMAGE_MODELS:
        try:
            return (
                generate_pollinations_product_image_with_model(
                    title, brand, category, model, prompt_hint
                ),
                model,
            )
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            errors.append(str(exc))

    raise RuntimeError("Pollinations failed: " + " | ".join(errors))


def build_payload(img_b64: str, mime: str) -> dict:
    schema = {
        "type": "OBJECT",
        "properties": {
            "product": {"type": "STRING"},
            "brand": {"type": "STRING"},
            "place": {"type": "STRING"},
            "extra": {"type": "STRING"},
            "total": {"type": "NUMBER"},
            "usage_rate_guess": {"type": "NUMBER"},
        },
        "required": ["product", "brand", "place", "extra", "total", "usage_rate_guess"],
    }

    return {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {"inline_data": {"mime_type": mime, "data": img_b64}},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": 0.2,
        },
    }


PROMPT = """
Ты анализируешь фото бытового товара, еды, косметики, одежды, техники или другого продукта для домашнего учета.
Верни только JSON-объект без markdown и текста вокруг.

Нужные поля:
- product: короткое название товара на русском
- brand: бренд, если виден; иначе "Без бренда"
- place: где обычно хранится дома, например "Ванная", "Кухня", "Дом", "Гардероб", "Уборка"
- extra: короткое понятное описание
- total: примерное количество единиц в упаковке, если видно; иначе 1
- usage_rate_guess: примерный расход в единицах в день для обычного дома из 2 человек

Правила:
- Если это одежда, верни тип одежды и бренд/надпись, если они видны.
- Если это одноразка, vape или e-cig, так и напиши.
- Если бренд виден, верни его максимально точно.
- Если не уверен, все равно дай лучший разумный вариант.
- total должно быть числом.
- usage_rate_guess должно быть числом от 0.01 до 3.
""".strip()


def gemini_generate_content(payload: dict) -> tuple[dict, str, str]:
    errors = []
    seen = set()
    models = [model for model in GEMINI_RECOGNITION_MODELS if model]
    loose_payload = {
        "contents": payload.get("contents", []),
        "generationConfig": {"temperature": 0.2},
    }
    payload_attempts = [("schema", payload), ("loose", loose_payload)]

    for model in models:
        if model in seen:
            continue
        seen.add(model)

        for version in GEMINI_API_VERSIONS:
            for attempt_name, attempt_payload in payload_attempts:
                url = (
                    f"https://generativelanguage.googleapis.com/{version}/models/"
                    f"{model}:generateContent"
                )
                try:
                    response = HTTP.post(
                        url,
                        headers={
                            "x-goog-api-key": GEMINI_API_KEY,
                            "Content-Type": "application/json",
                        },
                        json=attempt_payload,
                        timeout=60,
                    )
                except requests.RequestException as exc:
                    errors.append(f"{version}/{model}/{attempt_name}: {exc}")
                    continue

                try:
                    data = response.json()
                except ValueError:
                    data = {"error": {"message": response.text[:500]}}

                if response.status_code < 400:
                    return data, model, version

                message = stringify_error(data)
                errors.append(
                    f"{version}/{model}/{attempt_name}: HTTP {response.status_code}: {message}"
                )
                if response.status_code not in (400, 404, 429, 503, 504):
                    raise RuntimeError(
                        f"Gemini recognition failed: HTTP {response.status_code}: {message}"
                    )

    raise RuntimeError("Gemini recognition failed. " + " | ".join(errors))


def parse_json_text(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def fallback_recognition(reason: str = "") -> dict:
    return {
        "product": "Товар с фото",
        "brand": "Без бренда",
        "place": "Дом",
        "extra": "AI временно не распознал товар. Проверьте и исправьте вручную.",
        "total": 1,
        "usage_rate_guess": 0.4,
        "ai_warning": reason[:700] if reason else "AI recognition fallback",
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"items": [], "photos": {}}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": [], "photos": {}}


def save_state(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_frontend_source() -> str:
    return FRONTEND_FILE.read_text(encoding="utf-8")


def save_frontend_source(content: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = DATA_DIR / "index.backup.html"
    if FRONTEND_FILE.exists():
        backup_file.write_text(FRONTEND_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    FRONTEND_FILE.write_text(content, encoding="utf-8")


def restore_frontend_backup() -> bool:
    backup_file = DATA_DIR / "index.backup.html"
    if not backup_file.exists():
        return False
    FRONTEND_FILE.write_text(backup_file.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def get_backup_file() -> Path:
    return DATA_DIR / "index.backup.html"


def get_local_urls(port: int = 8000) -> list[str]:
    urls = {f"http://127.0.0.1:{port}"}

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if "." in ip and not ip.startswith("127."):
                urls.add(f"http://{ip}:{port}")
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if "." in ip and not ip.startswith("127."):
                urls.add(f"http://{ip}:{port}")
    except OSError:
        pass

    return sorted(urls)


def key_source_label() -> str:
    if os.getenv("GEMINI_API_KEY", "").strip():
        return "env"
    return "file" if KEY_FILE.exists() else "missing"


def pollinations_key_source_label() -> str:
    if os.getenv("POLLINATIONS_API_KEY", "").strip():
        return "env"
    return "file" if POLLINATIONS_KEY_FILE.exists() else "missing"


def masked_api_key() -> str:
    if not GEMINI_API_KEY:
        return ""
    if len(GEMINI_API_KEY) <= 14:
        return "***"
    return f"{GEMINI_API_KEY[:8]}...{GEMINI_API_KEY[-6:]}"


def masked_pollinations_key() -> str:
    if not POLLINATIONS_API_KEY:
        return ""
    if len(POLLINATIONS_API_KEY) <= 12:
        return "***"
    return f"{POLLINATIONS_API_KEY[:6]}...{POLLINATIONS_API_KEY[-4:]}"


def stringify_error(data) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("error"), str):
            return data["error"]
        if isinstance(data.get("message"), str):
            return data["message"]
        inner = data.get("error")
        if isinstance(inner, dict):
            if isinstance(inner.get("message"), str):
                return inner["message"]
            if isinstance(inner.get("status"), str):
                return inner["status"]
    return "Backend error"


def image_generation_error_response(exc: Exception) -> JSONResponse:
    message = str(exc) or "Gemini image generation failed"
    lower = message.lower()
    hint = "Check Gemini image model access, billing, quota, and API key."
    status_code = 502
    code = "IMAGE_GENERATION_FAILED"

    if "quota" in lower or "resource_exhausted" in lower or "429" in lower:
        status_code = 429
        code = "IMAGE_QUOTA_DISABLED"
        hint = (
            "This API key currently has 0 quota for Gemini image generation. "
            "Enable billing / a paid plan in Google AI Studio, or use another key "
            "that has access to Gemini image models."
        )
    elif "pollinations api key" in lower or "requires a valid pollinations" in lower:
        status_code = 401
        code = "POLLINATIONS_KEY_REQUIRED"
        hint = (
            "GPT Image reference editing in Pollinations requires a Pollinations API key. "
            "Set POLLINATIONS_API_KEY, then restart the server or reload the environment."
        )
    elif "api key" in lower or "permission" in lower or "403" in lower:
        status_code = 403
        code = "IMAGE_MODEL_ACCESS_DENIED"
        hint = "Use a Gemini API key that has access to image generation models."
    elif "reference image" in lower or "image-to-image" in lower:
        status_code = 422
        code = "REFERENCE_IMAGE_REQUIRED"
        hint = (
            "The free Pollinations text-to-image fallback cannot see the camera photo. "
            "Use a real image-to-image provider such as Gemini image with billing/quota, "
            "or set ALLOW_APPROX_TEXT_IMAGE=1 if an approximate generated product is acceptable."
        )

    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": message,
            "code": code,
            "hint": hint,
            "image_provider": IMAGE_PROVIDER,
            "image_model": (
                POLLINATIONS_IMAGE_MODELS[0]
                if IMAGE_PROVIDER == "pollinations" and POLLINATIONS_IMAGE_MODELS
                else (GEMINI_IMAGE_MODELS[0] if GEMINI_IMAGE_MODELS else GEMINI_IMAGE_MODEL)
            ),
            "pollinations_models": POLLINATIONS_IMAGE_MODELS,
            "image_models": GEMINI_IMAGE_MODELS,
        },
    )


@app.get("/")
def index():
    return FileResponse(FRONTEND_FILE)


@app.get("/editor")
def editor():
    return FileResponse(EDITOR_FILE)


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(MANIFEST_FILE, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(SERVICE_WORKER_FILE, media_type="application/javascript")


@app.get("/health")
def health():
    return {
        "ok": True,
        "frontend_found": FRONTEND_FILE.exists(),
        "state_found": STATE_FILE.exists(),
        "key_set": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
        "recognition_models": GEMINI_RECOGNITION_MODELS,
        "recognition_api_versions": GEMINI_API_VERSIONS,
        "image_model": GEMINI_IMAGE_MODELS[0] if GEMINI_IMAGE_MODELS else GEMINI_IMAGE_MODEL,
        "image_models": GEMINI_IMAGE_MODELS,
        "image_provider": IMAGE_PROVIDER,
        "allow_approx_text_image": ALLOW_APPROX_TEXT_IMAGE,
        "pollinations_key_set": bool(POLLINATIONS_API_KEY),
        "pollinations_key_source": pollinations_key_source_label(),
        "pollinations_key_masked": masked_pollinations_key(),
        "pollinations_key_file": str(POLLINATIONS_KEY_FILE),
        "pollinations_reference_model": POLLINATIONS_REFERENCE_MODEL,
        "pollinations_model": POLLINATIONS_IMAGE_MODEL,
        "pollinations_models": POLLINATIONS_IMAGE_MODELS,
        "image_size": {"width": IMAGE_WIDTH, "height": IMAGE_HEIGHT},
        "image_cache_items": len(load_image_cache()),
        "allow_image_fallback": ALLOW_IMAGE_FALLBACK,
        "key_source": key_source_label(),
        "key_masked": masked_api_key(),
        "key_file": str(KEY_FILE),
    }


@app.get("/api/state")
def get_state():
    return load_state()


@app.get("/api/info")
def get_info():
    return {
        "urls": get_local_urls(),
        "key_set": bool(GEMINI_API_KEY),
        "key_source": key_source_label(),
        "key_masked": masked_api_key(),
        "model": GEMINI_MODEL,
        "recognition_models": GEMINI_RECOGNITION_MODELS,
        "recognition_api_versions": GEMINI_API_VERSIONS,
        "image_model": GEMINI_IMAGE_MODELS[0] if GEMINI_IMAGE_MODELS else GEMINI_IMAGE_MODEL,
        "image_models": GEMINI_IMAGE_MODELS,
        "image_provider": IMAGE_PROVIDER,
        "allow_approx_text_image": ALLOW_APPROX_TEXT_IMAGE,
        "pollinations_key_set": bool(POLLINATIONS_API_KEY),
        "pollinations_key_source": pollinations_key_source_label(),
        "pollinations_key_masked": masked_pollinations_key(),
        "pollinations_key_file": str(POLLINATIONS_KEY_FILE),
        "pollinations_reference_model": POLLINATIONS_REFERENCE_MODEL,
        "pollinations_model": POLLINATIONS_IMAGE_MODEL,
        "pollinations_models": POLLINATIONS_IMAGE_MODELS,
        "image_size": {"width": IMAGE_WIDTH, "height": IMAGE_HEIGHT},
        "image_cache_items": len(load_image_cache()),
        "allow_image_fallback": ALLOW_IMAGE_FALLBACK,
    }


@app.post("/api/reload-key")
def reload_key():
    global GEMINI_API_KEY, POLLINATIONS_API_KEY
    GEMINI_API_KEY = load_api_key()
    POLLINATIONS_API_KEY = load_pollinations_api_key()
    return {
        "ok": True,
        "key_set": bool(GEMINI_API_KEY),
        "key_source": key_source_label(),
        "key_masked": masked_api_key(),
        "key_file": str(KEY_FILE),
        "image_models": GEMINI_IMAGE_MODELS,
        "image_provider": IMAGE_PROVIDER,
        "allow_approx_text_image": ALLOW_APPROX_TEXT_IMAGE,
        "pollinations_key_set": bool(POLLINATIONS_API_KEY),
        "pollinations_key_source": pollinations_key_source_label(),
        "pollinations_key_masked": masked_pollinations_key(),
        "pollinations_key_file": str(POLLINATIONS_KEY_FILE),
        "pollinations_reference_model": POLLINATIONS_REFERENCE_MODEL,
        "pollinations_model": POLLINATIONS_IMAGE_MODEL,
        "pollinations_models": POLLINATIONS_IMAGE_MODELS,
        "image_size": {"width": IMAGE_WIDTH, "height": IMAGE_HEIGHT},
        "image_cache_items": len(load_image_cache()),
    }


@app.get("/api/editor/content")
def get_editor_content():
    return {
        "content": load_frontend_source(),
        "path": str(FRONTEND_FILE),
    }


@app.get("/api/editor/download")
def download_editor_content():
    return FileResponse(
        FRONTEND_FILE,
        media_type="text/html; charset=utf-8",
        filename="index.html",
    )


@app.get("/api/editor/download-backup")
def download_editor_backup():
    backup_file = get_backup_file()
    if not backup_file.exists():
        return JSONResponse(status_code=404, content={"error": "Backup file not found"})
    return FileResponse(
        backup_file,
        media_type="text/html; charset=utf-8",
        filename="index.backup.html",
    )


@app.post("/api/editor/content")
def save_editor_content(payload: dict = Body(...)):
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return JSONResponse(status_code=400, content={"error": "Content is empty"})

    save_frontend_source(content)
    return {
        "ok": True,
        "path": str(FRONTEND_FILE),
        "size": len(content),
    }


@app.post("/api/editor/replace")
async def replace_editor_content(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "Empty file"})

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse(status_code=400, content={"error": "Only UTF-8 HTML files are supported"})

    if "<html" not in content.lower() and "<!doctype" not in content.lower():
        return JSONResponse(status_code=400, content={"error": "This does not look like an HTML file"})

    save_frontend_source(content)
    return {
        "ok": True,
        "path": str(FRONTEND_FILE),
        "size": len(content),
        "filename": file.filename or "index.html",
    }


@app.post("/api/editor/restore")
def restore_editor_content():
    ok = restore_frontend_backup()
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Backup file not found"})
    return {"ok": True, "path": str(FRONTEND_FILE)}


@app.post("/api/state")
def update_state(payload: dict = Body(...)):
    state = {
        "items": payload.get("items") if isinstance(payload.get("items"), list) else [],
        "photos": payload.get("photos")
        if isinstance(payload.get("photos"), dict)
        else {},
    }
    save_state(state)
    return {"ok": True}


@app.post("/api/product-image")
async def generate_product_image(
    file: UploadFile = File(...),
    title: str = Form(""),
    brand: str = Form(""),
    category: str = Form(""),
    prompt_hint: str = Form(""),
):
    raw = await file.read()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "Empty file"})

    mime = detect_mime(raw)
    product_title = (title or "Product").strip()
    now = datetime.now(timezone.utc).isoformat()
    original_image = image_data_url(raw, mime)
    warning = ""
    used_image_model = ""
    try:
        if IMAGE_PROVIDER == "pollinations":
            if ALLOW_APPROX_TEXT_IMAGE:
                generated_image, used_image_model = generate_pollinations_product_image(
                    product_title, brand, category, prompt_hint
                )
            elif POLLINATIONS_API_KEY:
                generated_image, used_image_model = generate_pollinations_reference_image(
                    raw, mime, product_title, brand, category
                )
            else:
                raise RuntimeError(
                    "Pollinations API key is required for GPT Image reference edit. Free text-to-image cannot preserve the exact product from the camera photo."
                )
            source = "pollinations_image"
        else:
            generated_image, used_image_model = generate_gemini_product_image(
                raw=raw,
                mime=mime,
                title=product_title,
                brand=brand,
                category=category,
            )
            source = "gemini_image"
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        if not ALLOW_IMAGE_FALLBACK:
            return image_generation_error_response(exc)
        generated_image = build_product_card_svg(
            product_title, brand, category, original_image
        )
        source = "fallback_product_card"
        warning = str(exc)

    # The data contract is intentionally explicit: the original camera photo is
    # preserved untouched, while generated_image is a separate product-card cover.
    return {
        "id": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        "title": product_title,
        "original_image": original_image,
        "generated_image": generated_image,
        "created_at": now,
        "source": source,
        "image_model": used_image_model,
        "warning": warning,
    }


@app.post("/recognize")
async def recognize(file: UploadFile = File(...)):
    if not GEMINI_API_KEY:
        return fallback_recognition("Gemini API key is missing")

    raw = await file.read()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "Empty file"})

    mime = detect_mime(raw)
    img_b64 = base64.b64encode(raw).decode("utf-8")
    payload = build_payload(img_b64, mime)

    try:
        data, used_model, used_version = gemini_generate_content(payload)
    except RuntimeError as exc:
        return fallback_recognition(str(exc))

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = parse_json_text(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return fallback_recognition("Could not parse Gemini response")

    parsed["product"] = str(parsed.get("product") or "Не распознано").strip()
    parsed["brand"] = str(parsed.get("brand") or "Без бренда").strip()
    parsed["place"] = str(parsed.get("place") or "Дом").strip()
    parsed["extra"] = str(parsed.get("extra") or "Проверь и исправь вручную").strip()

    try:
        parsed["total"] = max(1, int(float(parsed.get("total") or 1)))
    except (TypeError, ValueError):
        parsed["total"] = 1

    try:
        parsed["usage_rate_guess"] = min(
            3, max(0.01, float(parsed.get("usage_rate_guess") or 0.4))
        )
    except (TypeError, ValueError):
        parsed["usage_rate_guess"] = 0.4

    parsed["recognition_model"] = used_model
    parsed["recognition_api_version"] = used_version
    return parsed
