"""
Bot: Mercado Livre (ofertas) -> imagem de pin (Pillow) -> Pinterest API v5

Uso:
    pip install requests pillow
    export PINTEREST_TOKEN="..."      # token com escopos boards:read, pins:read, pins:write
    export PINTEREST_BOARD_ID="..."   # ID do board de destino
    export ML_TOKEN="..."             # token do app ML (a busca pode exigir)
    export AFFILIATE_SUFFIX="..."     # parametros do seu link de afiliado (veja README no topo)
    python ml_pinterest_bot.py

Teste primeiro com DRY_RUN=1 (gera imagens em ./pins sem postar).
"""
import base64
import io
import json
import os
import random
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIGURACAO ----------
QUERIES = ["organizador de cozinha", "presente criativo", "luminaria led"]
MIN_DISCOUNT = 20          # desconto minimo (%)
MIN_PRICE, MAX_PRICE = 20, 500
PINS_PER_RUN = 3           # poucos por execucao evita parecer spam
DRY_RUN = os.getenv("DRY_RUN") == "1"

PINTEREST_TOKEN = os.getenv("PINTEREST_TOKEN", "")
BOARD_ID = os.getenv("PINTEREST_BOARD_ID", "")
ML_TOKEN = os.getenv("ML_TOKEN", "")
# Como montar o link de afiliado: copie um link gerado no painel de afiliados
# do Mercado Livre e veja quais parametros ele adiciona. Coloque-os aqui.
AFFILIATE_SUFFIX = os.getenv("AFFILIATE_SUFFIX", "")

# Use https://api-sandbox.pinterest.com enquanto estiver em acesso Trial
PINTEREST_API = os.getenv("PINTEREST_API", "https://api.pinterest.com/v5")

STATE_FILE = Path("posted.json")
OUT_DIR = Path("pins")
OUT_DIR.mkdir(exist_ok=True)


# ---------- ESTADO (evita repetir produto) ----------
def load_posted():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_posted(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


# ---------- MERCADO LIVRE ----------
def search_offers(query):
    headers = {"Authorization": f"Bearer {ML_TOKEN}"} if ML_TOKEN else {}
    r = requests.get(
        "https://api.mercadolibre.com/sites/MLB/search",
        params={"q": query, "limit": 50},
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    offers = []
    for it in r.json().get("results", []):
        price, orig = it.get("price"), it.get("original_price")
        if not price or not orig or orig <= price:
            continue
        discount = round((1 - price / orig) * 100)
        if discount < MIN_DISCOUNT or not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        offers.append({
            "id": it["id"],
            "title": it["title"],
            "price": price,
            "orig": orig,
            "discount": discount,
            "thumb": it["thumbnail"].replace("-I.jpg", "-O.jpg"),  # versao maior
            "url": it["permalink"],
        })
    return offers


def affiliate_link(url):
    if not AFFILIATE_SUFFIX:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{AFFILIATE_SUFFIX.lstrip('?&')}"


# ---------- IMAGEM DO PIN (1000x1500) ----------
def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def wrap(draw, text, fnt, max_w):
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=fnt) <= max_w:
            line = test
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines[:3]


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def make_pin(offer):
    W, H = 1000, 1500
    img = Image.new("RGB", (W, H), "#FFF8E7")
    d = ImageDraw.Draw(img)

    photo = Image.open(io.BytesIO(requests.get(offer["thumb"], timeout=20).content)).convert("RGB")
    photo.thumbnail((900, 900))
    img.paste(photo, ((W - photo.width) // 2, 80))

    # selo de desconto
    d.ellipse((730, 40, 960, 270), fill="#E63946")
    d.text((845, 155), f"-{offer['discount']}%", font=font(70, True), fill="white", anchor="mm")

    y = 1020
    for line in wrap(d, offer["title"], font(52, True), 900):
        d.text((50, y), line, font=font(52, True), fill="#222")
        y += 66
    y += 20
    d.text((50, y), brl(offer["orig"]), font=font(40), fill="#888")
    w = d.textlength(brl(offer["orig"]), font=font(40))
    d.line((50, y + 22, 50 + w, y + 22), fill="#888", width=3)
    d.text((50, y + 55), brl(offer["price"]), font=font(90, True), fill="#2A9D8F")

    path = OUT_DIR / f"{offer['id']}.jpg"
    img.save(path, quality=90)
    return path


# ---------- PINTEREST ----------
def post_pin(offer, image_path):
    data = base64.b64encode(image_path.read_bytes()).decode()
    body = {
        "board_id": BOARD_ID,
        "title": offer["title"][:100],
        "description": (
            f"{offer['title']} com {offer['discount']}% de desconto. "
            "Oferta do Mercado Livre. (Link de afiliado)"
        )[:500],
        "link": affiliate_link(offer["url"]),
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/jpeg",
            "data": data,
        },
    }
    r = requests.post(
        f"{PINTEREST_API}/pins",
        headers={"Authorization": f"Bearer {PINTEREST_TOKEN}"},
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("id")


# ---------- MAIN ----------
def main():
    if not DRY_RUN and not (PINTEREST_TOKEN and BOARD_ID):
        sys.exit("Defina PINTEREST_TOKEN e PINTEREST_BOARD_ID (ou use DRY_RUN=1).")

    posted = load_posted()
    pool = []
    for q in QUERIES:
        try:
            pool += [o for o in search_offers(q) if o["id"] not in posted]
        except requests.RequestException as e:
            print(f"Falha na busca '{q}': {e}")
    random.shuffle(pool)

    count = 0
    for offer in pool:
        if count >= PINS_PER_RUN:
            break
        try:
            img = make_pin(offer)
            if DRY_RUN:
                print(f"[dry-run] {img} -> {affiliate_link(offer['url'])}")
            else:
                pin_id = post_pin(offer, img)
                print(f"Pin criado: {pin_id} ({offer['title'][:50]})")
            posted.add(offer["id"])
            count += 1
        except Exception as e:
            print(f"Erro em {offer['id']}: {e}")

    save_posted(posted)
    print(f"Concluido: {count} pin(s).")


if __name__ == "__main__":
    main()
