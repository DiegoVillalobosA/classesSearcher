import os, json, hashlib, time, random, urllib.request, urllib.parse, sys, traceback
from playwright.sync_api import sync_playwright

# ------------- CONFIG BÁSICA -------------
# Si GitHub te pasa URL por env la usa; si no, usa la de ASU
URL = os.getenv("URL", "https://catalog.apps.asu.edu/catalog/classes")

# Aquí pones las clases que quieres vigilar
# En el workflow las podemos sobreescribir, pero esto sirve de default
QUERIES = json.loads(os.getenv(
    "QUERIES_JSON",
    '[{"subject":"CSE","number":"412","term":"Spring 2026"}]'
))

# Selectores: estos son PLACEHOLDER porque la página de ASU es React.
# Luego los ajustas con lo que veas en el inspector / playwright codegen.
SEL = json.loads(os.getenv(
    "SELECTORS_JSON",
    # ojo con las comillas escapadas
    '{"subject":"input[name=\\"subject\\"]",'
    '"number":"input[name=\\"number\\"]",'
    '"term":"select[name=\\"term\\"]",'
    '"search":"button:has-text(\\"Search\\")",'
    '"table":"table.resultados"}'
))

STATE = "state.json"

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

# ------------- UTILIDADES -------------
def hash_rows(rows):
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()

def notify(text: str):
    """Manda notificación a Telegram o imprime si no hay token/chat."""
    if TG_TOKEN and TG_CHAT:
        try:
            data = urllib.parse.urlencode({
                "chat_id": TG_CHAT,
                "text": text
            }).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=data,
                timeout=10
            )
            return
        except Exception:
            # si falla telegram, cae al print
            pass
    print("NOTIFY:", text)

def extract_rows(page):
    """Lee la tabla y devuelve filas normalizadas.
       Ajusta esto a la estructura real de la tabla de ASU."""
    page.wait_for_selector(SEL["table"], timeout=60000)
    rows = []
    for tr in page.query_selector_all(f'{SEL["table"]} tbody tr'):
        tds = tr.query_selector_all("td")
        # ajusta si tu tabla tiene más o menos columnas
        if len(tds) < 5:
            continue
        rows.append({
            "nrc":     tds[0].inner_text().strip(),
            "course":  tds[1].inner_text().strip(),
            "seats":   tds[2].inner_text().strip(),
            "wait":    tds[3].inner_text().strip(),
            "time":    tds[4].inner_text().strip(),
        })
    return rows

# ------------- MAIN -------------
def run():
    # pequeño jitter para no pegarle en el mismo segundo siempre
    time.sleep(random.uniform(0, 5))

    with sync_playwright() as p:
        browser = p.chromium.launch()  # headless por defecto
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        all_rows = []

        for q in QUERIES:
            # Rellena el formulario según tus nombres de campo reales
            # (tú ya sabes que solo necesitas subject, number y term)
            page.fill(SEL["subject"], q["subject"])
            page.fill(SEL["number"],  q["number"])
            # term es select
            page.select_option(SEL["term"], label=q["term"])
            # click en buscar
            page.click(SEL["search"])

            rows = extract_rows(page)
            # marca de qué query vino cada fila
            for r in rows:
                r["_query"] = f'{q["subject"]}{q["number"]}-{q["term"]}'
            all_rows.extend(rows)

        browser.close()

    # snapshot nuevo
    new_state = {
        "hash": hash_rows(all_rows),
        "rows": all_rows,
        "ts": int(time.time())
    }

    # carga viejo
    try:
        old_state = json.load(open(STATE, "r"))
    except Exception:
        old_state = {"hash": None, "rows": []}

    if old_state.get("hash") != new_state["hash"]:
        # hay cambios → construir diff básico
        old_map = {r["nrc"]: r for r in old_state.get("rows", []) if "nrc" in r}
        new_map = {r["nrc"]: r for r in new_state["rows"] if "nrc" in r}

        added   = [new_map[k] for k in new_map.keys() - old_map.keys()]
        removed = [old_map[k] for k in old_map.keys() - new_map.keys()]
        changed = []
        for k in new_map.keys() & old_map.keys():
            o, n = old_map[k], new_map[k]
            if (o.get("seats"), o.get("wait"), o.get("time")) != (n.get("seats"), n.get("wait"), n.get("time")):
                changed.append((o, n))

        lines = []
        for r in added[:6]:
            lines.append(f'➕ NRC {r["nrc"]} {r["course"]} | Seats {r["seats"]} | Wait {r["wait"]}')
        for r in removed[:6]:
            lines.append(f'➖ NRC {r["nrc"]} {r["course"]}')
        for o, n in changed[:6]:
            lines.append(
                f'🔁 NRC {n["nrc"]} {n["course"]}: '
                f'Seats {o["seats"]}→{n["seats"]}, '
                f'Wait {o["wait"]}→{n["wait"]}'
            )
        msg = "\n".join(lines) or "Class watcher: detected changes."
        notify(msg)

        # guarda nuevo estado
        with open(STATE, "w") as f:
            json.dump(new_state, f)
        print("CHANGED")
    else:
        print("NOCHANGE")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        print("ERROR:\n", traceback.format_exc())
        sys.exit(1)