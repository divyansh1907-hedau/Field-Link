# app.py
# FieldLink – single file Flask app (HTML+CSS+JS in one file)
# ------------------------------------------------------------
# WHAT'S NEW:
# - Universal Key Manager (env vars or keys/keys.json)
# - One universal AI function: ai_generate(prompt) with fallback
#   Order: Gemini (primary) → HuggingFace → OpenAI
# - /api/gemini now uses ai_generate(), so no frontend changes needed
# - Optional /api/keys (GET/POST) to view or set keys at runtime
# - Expanded fallback crop list to 80+ items

import os
import json
import random
import threading
import webbrowser
from datetime import datetime
from urllib.parse import quote_plus

import requests
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)

# ----------- CONSTANTS / DIRS -----------
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# keys live outside DATA_DIR so they don't get swept with saved crops
KEYS_DIR = os.path.join(BASE_DIR, "keys")
KEYS_PATH = os.path.join(KEYS_DIR, "keys.json")
os.makedirs(KEYS_DIR, exist_ok=True)

# Government “Agmarknet” dataset (commodities)
# Docs: https://api.data.gov.in/
AGMARK_DATASET = "9ef84268-d588-465a-a308-a864a43d0070"

# ----------- DEFAULTS / ENV KEYS (used if not in keys.json) -----------
# You can export env vars GEMINI_KEY, HUGGINGFACE_KEY, OPENAI_API_KEY, DATAGOV_KEY, OPENWEATHER_KEY
ENV_GEMINI = os.getenv("GEMINI_KEY", "AIzaSyCW3_bjlw-ghjssXbT-ScKpWhoRQuZSWNw")
ENV_HF = os.getenv("HUGGINGFACE_KEY", "AIzaSyD1hr4eiTwVUU7DWF3VjjUjFQY3hHc9tEk")
ENV_OPENAI = os.getenv("OPENAI_API_KEY", "AIzaSyCLVidgzdC6SuecKD6eYsfFGEGLUwAhXOs")
ENV_DATAGOV = os.getenv("DATAGOV_KEY", "579b464db66ec23bdd000001a222159ec4f843a04510d7b33c7e553f")
ENV_OPENWEATHER = os.getenv("OPENWEATHER_KEY", "7b4eb2c7244705f2337c79fc004208d3")

# ----------- KEY MANAGER -----------
def _load_keys_file():
    if not os.path.exists(KEYS_PATH):
        # initialize with empty keys file on first run
        try:
            with open(KEYS_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "gemini": ENV_GEMINI,
                        "huggingface": ENV_HF,
                        "openai": ENV_OPENAI,
                        "datagov": ENV_DATAGOV,
                        "openweather": ENV_OPENWEATHER,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception:
            pass
    try:
        with open(KEYS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # fallback purely to env if file unreadable
        return {
            "gemini": ENV_GEMINI,
            "huggingface": ENV_HF,
            "openai": ENV_OPENAI,
            "datagov": ENV_DATAGOV,
            "openweather": ENV_OPENWEATHER,
        }

def _save_keys_file(keys_dict):
    try:
        with open(KEYS_PATH, "w", encoding="utf-8") as f:
            json.dump(keys_dict, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def get_api_key(service: str) -> str:
    """
    Universal function to get API key for a service.
    Services: 'gemini', 'huggingface', 'openai', 'datagov', 'openweather'
    Priority: keys.json overrides env (env is used to seed keys.json initially).
    """
    keys = _load_keys_file()
    val = (keys or {}).get(service, "") or ""
    if not val:  # fallback to env, just in case
        if service == "gemini":
            return ENV_GEMINI
        if service == "huggingface":
            return ENV_HF
        if service == "openai":
            return ENV_OPENAI
        if service == "datagov":
            return ENV_DATAGOV
        if service == "openweather":
            return ENV_OPENWEATHER
    return val

# expose these to existing code paths
OPENWEATHER_KEY = get_api_key("openweather")
DATAGOV_KEY = get_api_key("datagov")

# ----------- SMALL HELPERS -----------
def ok(data, code=200):
    return make_response(jsonify(data), code)

def fail(msg, code=400):
    return make_response(jsonify({"error": msg}), code)

def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def parse_arrival_date(s):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None

def datagov(params):
    """Call data.gov.in with sensible defaults & timeouts."""
    url = f"https://api.data.gov.in/resource/{AGMARK_DATASET}"
    base = {
        "api-key": DATAGOV_KEY,
        "format": "json",
        "limit": "2000",
    }
    base.update(params or {})
    try:
        r = requests.get(url, params=base, timeout=15)
        r.raise_for_status()
        return r.json().get("records", [])
    except Exception as e:
        print("data.gov error:", e)
        return []

def varieties_from_records(records):
    seen = set()
    for r in records:
        v = (r.get("variety") or "").strip()
        if v:
            seen.add(v)
    return sorted(seen)

def perkg_stats_for_latest(records):
    usable = []
    for r in records:
        dt = parse_arrival_date(r.get("arrival_date", ""))
        if not dt:
            continue
        min_p = safe_float(r.get("min_price"))
        max_p = safe_float(r.get("max_price"))
        modal_p = safe_float(r.get("modal_price"))
        if min_p is None and max_p is None and modal_p is None:
            continue
        usable.append((dt, min_p, max_p, modal_p, r))
    if not usable:
        return None

    latest = max(u[0] for u in usable)
    today = [u for u in usable if u[0] == latest]

    # Convert Rs/quintal => Rs/kg
    tokg = lambda v: (v / 100.0) if (v is not None) else None

    mins = [tokg(u[1]) for u in today if u[1] is not None]
    maxs = [tokg(u[2]) for u in today if u[2] is not None]
    modals = [tokg(u[3]) for u in today if u[3] is not None]

    def median(values):
        vals = sorted(values)
        n = len(vals)
        if n == 0:
            return None
        mid = n // 2
        if n % 2 == 1:
            return vals[mid]
        return (vals[mid - 1] + vals[mid]) / 2.0

    markets = []
    for u in today:
        r = u[4]
        markets.append({
            "market": r.get("market"),
            "district": r.get("district"),
            "state": r.get("state"),
            "min_perkg": tokg(safe_float(r.get("min_price")) or 0),
            "max_perkg": tokg(safe_float(r.get("max_price")) or 0),
            "modal_perkg": tokg(safe_float(r.get("modal_price")) or 0),
        })

    return {
        "date": latest.isoformat(),
        "min_perkg": min(mins) if mins else None,
        "max_perkg": max(maxs) if maxs else None,
        "median_modal_perkg": median(modals) if modals else None,
        "sample_count": len(today),
        "markets": markets
    }

def get_user_ip_city_state():
    """Best-effort IP → (city, state)."""
    try:
        r = requests.get("https://ipinfo.io/json", timeout=6)
        js = r.json()
        return js.get("city"), js.get("region")
    except Exception:
        return None, None

# ----------- UNIVERSAL AI (PRIMARY GEMINI → HF → OPENAI) -----------
def ai_generate(prompt: str) -> dict:
    """
    Returns: {"text": "...", "provider": "gemini|huggingface|openai", "error": None|"..."}
    Tries Gemini first, then Hugging Face, then OpenAI.
    No exceptions bubble out; you always get a dictionary.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"text": "", "provider": None, "error": "empty prompt"}

    # 1) Gemini (primary)
    gemini_key = get_api_key("gemini")
    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            body = { "contents": [ { "parts": [ { "text": prompt } ] } ] }
            r = requests.post(url, json=body, timeout=30)
            if r.status_code == 200:
                data = r.json()
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    text = json.dumps(data)
                return {"text": text, "provider": "gemini", "error": None}
            else:
                # If quota/429 or transient → fall through
                print("Gemini fallback, status:", r.status_code, r.text[:200])
        except Exception as e:
            print("Gemini call error:", e)

    # 2) Hugging Face (Inference API) – simple text generation model
    hf_key = get_api_key("huggingface")
    if hf_key:
        try:
            # Use a broadly available instruction-tuned model
            # You can swap to a preferred hosted model
            url = "https://api-inference.huggingface.co/models/google/flan-t5-large"
            headers = {"Authorization": f"Bearer {hf_key}"}
            payload = {"inputs": prompt, "parameters": {"max_new_tokens": 256}}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                data = r.json()
                # HF can return either a list of dicts [{"generated_text": "..."}] or a dict
                text = ""
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    # Some models return "generated_text", some "generated_token_count"
                    text = data[0].get("generated_text") or data[0].get("summary_text") or json.dumps(data[0])
                elif isinstance(data, dict):
                    text = data.get("generated_text") or data.get("summary_text") or json.dumps(data)
                else:
                    text = json.dumps(data)
                return {"text": text, "provider": "huggingface", "error": None}
            else:
                print("HF fallback, status:", r.status_code, r.text[:200])
        except Exception as e:
            print("HF call error:", e)

    # 3) OpenAI – Chat Completions API
    openai_key = get_api_key("openai")
    if openai_key:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "You are a concise assistant for farmers (Tamil+English mix)."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 512,
            }
            r = requests.post(url, headers=headers, json=body, timeout=45)
            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                return {"text": text, "provider": "openai", "error": None}
            else:
                print("OpenAI failed:", r.status_code, r.text[:200])
        except Exception as e:
            print("OpenAI call error:", e)

    return {"text": "", "provider": None, "error": "All providers failed or keys missing"}

# ----------- API ROUTES -----------
@app.route("/")
def home():
    return HTML_PAGE

@app.route("/api/keys", methods=["GET", "POST"])
def api_keys():
    """
    GET → returns which keys exist (boolean only).
    POST → set any subset of keys: {"gemini":"...", "huggingface":"...", "openai":"...", "datagov":"...", "openweather":"..."}
    """
    if request.method == "GET":
        keys = _load_keys_file()
        return ok({
            "has": {
                "gemini": bool(keys.get("gemini")),
                "huggingface": bool(keys.get("huggingface")),
                "openai": bool(keys.get("openai")),
                "datagov": bool(keys.get("datagov")),
                "openweather": bool(keys.get("openweather")),
            }
        })
    try:
        incoming = request.get_json(force=True) or {}
    except Exception:
        incoming = {}
    keys = _load_keys_file()
    keys.update({k: v for k, v in incoming.items() if k in {"gemini","huggingface","openai","datagov","openweather"}})
    saved = _save_keys_file(keys)
    # Refresh globals that old code references
    global DATAGOV_KEY, OPENWEATHER_KEY
    DATAGOV_KEY = get_api_key("datagov")
    OPENWEATHER_KEY = get_api_key("openweather")
    return ok({"saved": bool(saved)})

@app.route("/api/weather")
def api_weather():
    # Accept either ?city=, or ?lat=&lon=
    city = request.args.get("city")
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    try:
        if city:
            url = f"https://api.openweathermap.org/data/2.5/weather?q={quote_plus(city)}&appid={OPENWEATHER_KEY}&units=metric"
        elif lat and lon:
            url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_KEY}&units=metric"
        else:
            # fallback by IP
            ip_city, _ = get_user_ip_city_state()
            q = ip_city or "Chennai,IN"
            url = f"https://api.openweathermap.org/data/2.5/weather?q={quote_plus(q)}&appid={OPENWEATHER_KEY}&units=metric"

        r = requests.get(url, timeout=10)
        r.raise_for_status()
        js = r.json()
        out = {
            "temp": js["main"]["temp"],
            "pressure": js["main"]["pressure"],
            "humidity": js["main"]["humidity"],
            "condition": js["weather"][0]["description"],
            "city": js.get("name")
        }
        return ok(out)
    except Exception as e:
        print("weather error:", e)
        return ok({"temp": 27.0, "pressure": 1010, "humidity": 70, "condition": "clear sky", "city": "—"})

@app.route("/api/random_price")
def api_random_price():
    try:
        records = datagov({"limit": "200"})
        if not records:
            return ok({"commodity": "—", "state": "—", "modal_price": 0})
        rec = random.choice(records)
        return ok({
            "commodity": rec.get("commodity"),
            "state": rec.get("state"),
            "district": rec.get("district"),
            "market": rec.get("market"),
            "modal_price": rec.get("modal_price"),
            "arrival_date": rec.get("arrival_date")
        })
    except Exception as e:
        print("random_price error:", e)
        return ok({"commodity": "—", "state": "—", "modal_price": 0})

@app.route("/api/varieties")
def api_varieties():
    crop = (request.args.get("crop") or "").strip()
    state = (request.args.get("state") or "").strip() or None
    if not crop:
        return ok({"varieties": []})
    # try state filter on server (dataset has field 'state')
    params = {"limit": "2000"}
    if state:
        params["filters[state]"] = state
    recs = datagov(params)
    # coarse match on commodity text
    n = crop.lower()
    recs = [r for r in recs if n in (r.get("commodity") or "").lower()]
    if not recs and state:
        recs = datagov({"limit": "2000"})  # fallback India-wide
        recs = [r for r in recs if n in (r.get("commodity") or "").lower()]
    varieties = varieties_from_records(recs)
    return ok({"varieties": varieties})

@app.route("/api/price_stats")
def api_price_stats():
    crop = (request.args.get("crop") or "").strip()
    variety = (request.args.get("variety") or "").strip()
    state = (request.args.get("state") or "").strip()
    if not crop:
        return fail("crop required")
    # Pull large set; filter locally for robustness
    params = {"limit": "2000"}
    if state:
        params["filters[state]"] = state
    recs = datagov(params)
    crop_l = crop.lower()
    recs = [r for r in recs if crop_l in (r.get("commodity") or "").lower()]
    if variety:
        v_l = variety.lower()
        recs = [r for r in recs if v_l == (r.get("variety") or "").lower()]
    # If nothing and we used state, try India-wide fallback
    if not recs and state:
        recs = datagov({"limit": "2000"})
        recs = [r for r in recs if crop_l in (r.get("commodity") or "").lower()]
        if variety:
            recs = [r for r in recs if v_l == (r.get("variety") or "").lower()]
    stats = perkg_stats_for_latest(recs)
    if not stats:
        return ok({"date": None, "min_perkg": None, "max_perkg": None, "median_modal_perkg": None, "sample_count": 0, "markets": []})
    # Also group 5 districts sample for chart
    # Use modal price per kg by district
    district_map = {}
    for m in stats["markets"]:
        d = m.get("district") or "—"
        if m.get("modal_perkg") is None:
            continue
        district_map.setdefault(d, []).append(m["modal_perkg"])
    series = []
    for d, arr in district_map.items():
        series.append({"district": d, "avg_modal_perkg": sum(arr)/len(arr)})
    # limit to top 5 by average
    series = sorted(series, key=lambda x: x["avg_modal_perkg"], reverse=True)[:5]
    stats["district_series"] = series
    return ok(stats)

@app.route("/api/saved_list")
def api_saved_list():
    items = []
    for name in os.listdir(DATA_DIR):
        f = os.path.join(DATA_DIR, name)
        if os.path.isdir(f):
            items.append(name)
    items.sort(reverse=True)
    return ok({"folders": items})

@app.route("/api/load_saved")
def api_load_saved():
    folder = request.args.get("folder")
    if not folder:
        return fail("folder required")
    d = os.path.join(DATA_DIR, folder)
    data_path = os.path.join(d, "crop.json")
    if not os.path.exists(data_path):
        return fail("no crop.json in folder")
    with open(data_path, "r", encoding="utf-8") as f:
        js = json.load(f)
    return ok(js)

@app.route("/api/save", methods=["POST"])
def api_save():
    try:
        payload = request.get_json(force=True)
    except Exception:
        payload = {}
    crop = (payload.get("crop") or "Crop").strip()
    variety = (payload.get("variety") or "Variety").strip()
    nowtag = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder_name = f"{crop}-{nowtag}"
    folder = os.path.join(DATA_DIR, folder_name)
    os.makedirs(folder, exist_ok=True)

    # Save crop.json
    with open(os.path.join(folder, "crop.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return ok({"saved": True, "folder": folder_name})

@app.route("/api/gemini", methods=["POST"])
def api_gemini():
    """
    Universal AI endpoint (kept same URL for frontend).
    Tries Gemini → HF → OpenAI, returns {"text": "...", "provider": "..."}.
    Request: {"prompt": "..."}
    """
    try:
        js = request.get_json(force=True)
    except Exception:
        js = {}
    prompt = (js.get("prompt") or "").strip()
    if not prompt:
        return fail("prompt required")

    result = ai_generate(prompt)
    # Always return shape {"text": "..."} to keep the frontend happy,
    # plus a debug provider tag.
    text = result.get("text") or (f"[AI error] {result.get('error') or 'Unknown error'}")
    return ok({"text": text, "provider": result.get("provider")})

# ----------- AUTO-OPEN BROWSER ----------
def _open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")

# ----------- HTML (INLINE) -----------
HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FieldLink</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3Ccircle cx='32' cy='32' r='32' fill='%232b7a78'/%3E%3Ctext x='32' y='40' font-size='28' text-anchor='middle' fill='white' font-family='Segoe UI' %3EFL%3C/text%3E%3C/svg%3E" />
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<style>
  :root{
    --brand:#2b7a78; --bg:#f4f7fa; --card:#ffffff; --ink:#333; --muted:#667085;
    --ok:#198754; --warn:#e6b800; --shadow:0 6px 20px rgba(0,0,0,.08);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:"Segoe UI",system-ui,Arial}
  header{background:var(--brand);color:#fff;padding:14px 18px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:10}
  .logo{display:flex;align-items:center;gap:10px}
  .logo .badge{width:40px;height:40px;border-radius:10px;background:#eaf6f6;color:#125;display:flex;align-items:center;justify-content:center;font-weight:700}
  .titlewrap{display:flex;flex-direction:column}
  .subtitle{font-size:12px;opacity:.9}
  .weather{margin-left:auto;display:flex;align-items:center;gap:12px}
  .wcard{background:#eaf6f6;color:#0a4140;border-radius:8px;padding:6px 10px;min-width:240px}
  .container{max-width:1200px;margin:18px auto;padding:0 16px 80px}
  .row{display:grid;gap:16px}
  .row.cards{grid-template-columns:repeat(5,minmax(160px,1fr))}
  .card{background:var(--card);border-radius:14px;box-shadow:var(--shadow);padding:16px}
  .pill{display:inline-flex;align-items:center;gap:8px;background:#eaf6f6;color:#0a4140;padding:8px 12px;border-radius:12px;font-weight:600}
  .navbtn{cursor:pointer;transition:transform .12s ease}
  .navbtn:hover{transform:translateY(-2px)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}
  .item{padding:14px;border-radius:12px;background:#fff;border:1px solid #eef;cursor:pointer;text-align:center;transition:.15s}
  .item:hover{background:#eaf6f6}
  .btn{border:0;background:var(--brand);color:#fff;border-radius:10px;padding:10px 14px;cursor:pointer}
  .btn.secondary{background:#eaf6f6;color:#0a4140}
  .btn.ghost{background:transparent;color:var(--brand);border:1px solid var(--brand)}
  .panel{display:grid;grid-template-columns:2fr 1fr;gap:16px}
  .sticky-right{position:sticky;top:86px;height:fit-content}
  .rightcard{background:var(--card);border-radius:14px;box-shadow:var(--shadow);padding:16px}
  .toast{position:fixed;right:16px;bottom:16px;background:#fff3cd;padding:12px 14px;border-radius:10px;box-shadow:var(--shadow)}
  .fab{position:fixed;left:16px;bottom:16px;width:56px;height:56px;border-radius:50%;background:var(--brand);color:#fff;display:flex;align-items:center;justify-content:center;font-size:22px;cursor:pointer;box-shadow:var(--shadow)}
  .fabpanel{position:fixed;left:16px;bottom:84px;width:280px;background:#fff;border-radius:14px;box-shadow:var(--shadow);display:none;padding:12px}
  .popup{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center}
  .pop{background:#fff;width:min(460px,92vw);border-radius:14px;box-shadow:var(--shadow);padding:18px}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  label{font-size:12px;color:var(--muted)}
  input,select{width:100%;padding:10px;border-radius:10px;border:1px solid #dde}
  hr{border:none;border-top:1px solid #eee;margin:12px 0}
  .spark{width:120px;height:28px}
  .price-list{font-size:13px}
  .price-list div{padding:4px 0;border-bottom:1px dashed #eee}
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="badge">FL</div>
    <div class="titlewrap">
      <div style="font-weight:700;font-size:18px">FieldLink</div>
      <div class="subtitle">Smart farming assistant </div>
    </div>
  </div>

  <div class="weather">
    <div class="wcard" id="weatherBox">Loading weather…</div>
    <canvas id="tempSpark" class="spark"></canvas>
    <canvas id="pressSpark" class="spark"></canvas>
  </div>
</header>

<div class="container">
  <div class="row cards">
    <div class="card navbtn" onclick="selectTab('harvest')"><span class="pill">Harvesting Analysis</span><div>Time, season, alerts</div></div>
    <div class="card navbtn" onclick="selectTab('crop')"><span class="pill">Crop Analysis</span><div>Disease, remedies</div></div>
    <div class="card navbtn" onclick="selectTab('market')"><span class="pill">Market Analysis</span><div>Prices & trends</div></div>
    <div class="card navbtn" onclick="selectTab('gov')"><span class="pill">Gov Helps</span><div>Schemes & support</div></div>
    <div class="card navbtn" onclick="selectTab('aifarmer')"><span class="pill">AI Farmer</span><div>Ask about your crop</div></div>
  </div>

  <div class="panel" style="margin-top:16px">
    <!-- Left -->
    <div class="card" id="mainPanel">
      <h2 id="panelTitle">Crop Analysis</h2>
      <div id="panelCrop">
        <div style="display:flex;gap:8px;margin-bottom:8px">
          <button class="btn" onclick="startCropFlow()">Choose Crop</button>
          <button class="btn secondary" onclick="loadSavedList()">Saved Data</button>
        </div>
        <div id="cropFlowHelp">Click “Choose Crop” to start. Or open a saved data from right panel.</div>
        <div id="cropList" class="grid" style="margin-top:10px;display:none"></div>
        <div id="varietyList" class="grid" style="margin-top:10px;display:none"></div>
      </div>

      <div id="panelMarket" style="display:none">
        <div class="row2">
          <div><label>Crop</label><input id="mkCrop" placeholder="e.g., Rice"></div>
          <div><label>Variety (optional)</label><input id="mkVariety" placeholder="e.g., 1009 Kar"></div>
        </div>
        <div class="row2" style="margin-top:8px">
          <div><label>State (optional)</label><input id="mkState" placeholder="e.g., Maharashtra"></div>
        </div>
        <div style="margin-top:10px"><button class="btn" onclick="runMarket()">Analyze Market</button></div>
        <div id="mkResult" style="margin-top:14px"></div>
        <canvas id="mkChart" height="140" style="margin-top:8px"></canvas>
      </div>

      <div id="panelGov" style="display:none">
        <div class="row2">
          <div style="grid-column:1/-1"><label>Ask about Government schemes </label></div>
          <div style="grid-column:1/-1"><input id="govQ" placeholder="Type your question…"></div>
        </div>
        <div style="margin-top:8px"><button class="btn" onclick="askGeminiGov()">Ask</button></div>
        <div id="govA" style="margin-top:12px;white-space:pre-wrap"></div>
      </div>

      <div id="panelAI" style="display:none">
        <div><label>Ask anything about your saved crop folder</label></div>
        <div class="row2">
          <input id="aiFolder" placeholder="folder name e.g., Rice-20250820-221306">
          <input id="aiQ" placeholder="Question....">
        </div>
        <div style="margin-top:8px"><button class="btn" onclick="askGeminiAI()">Ask AI Farmer</button></div>
        <div id="aiA" style="margin-top:12px;white-space:pre-wrap"></div>
      </div>

      <div id="panelHarvest" style="display:none">
        <div><label>Folder (saved crop)</label></div>
        <input id="hvFolder" placeholder="folder name e.g., Rice-20250820-221306">
        <div style="margin-top:8px"><button class="btn" onclick="runHarvest()">Run Harvesting Analysis</button></div>
        <div id="hvOut" style="margin-top:12px;white-space:pre-wrap"></div>
      </div>
    </div>

    <!-- Right -->
    <div class="sticky-right">
      <div class="rightcard">
        <div style="font-weight:700;margin-bottom:6px">Quick Market & Trends</div>
        <div id="qpBox" class="price-list">Loading…</div>
        <hr>
        <div style="font-weight:700;margin-bottom:6px">Recent Agri Trends</div>
        <div id="trends" style="font-size:13px;white-space:pre-wrap"></div>
      </div>

      <div class="rightcard" style="margin-top:12px">
        <div style="font-weight:700;margin-bottom:6px">Selected / Saved</div>
        <div id="selBlock">No selection</div>
        <hr>
        <div style="font-weight:700;margin-bottom:6px">Saved Analyses</div>
        <div id="savedList">Loading…</div>
      </div>
    </div>
  </div>
</div>

<!-- Floating chatbot -->
<div class="fab" id="fab">💬</div>
<div class="fabpanel" id="fabPanel">
  <div style="font-weight:700;margin-bottom:6px">Ask MiniBotix</div>
  <input id="miniQ" placeholder="Type your question…">
  <div style="margin-top:8px"><button class="btn" onclick="askMini()">Ask</button></div>
  <div id="miniA" style="margin-top:8px;white-space:pre-wrap"></div>
</div>

<div class="popup" id="popup">
  <div class="pop">
    <div style="font-weight:700" id="popTitle">Enter details</div>
    <div class="row2" style="margin-top:8px">
      <div><label>Area</label><input id="inArea" placeholder="e.g., 2.5"></div>
      <div><label>Unit</label>
        <select id="inUnit">
          <option value="acres">Acres</option>
          <option value="hectares">Hectares</option>
        </select>
      </div>
      <div style="grid-column:1/-1"><label>Suitable Soil</label>
        <select id="inSoil"></select>
      </div>
      <div style="grid-column:1/-1"><label>Seeding Date</label>
        <input type="date" id="inSeed">
      </div>
    </div>
    <div style="margin-top:10px;display:flex;gap:8px;justify-content:flex-end">
      <button class="btn ghost" onclick="closePop()">Cancel</button>
      <button class="btn" onclick="saveCrop()">Save</button>
    </div>
  </div>
</div>

<div id="toast" class="toast" style="display:none"></div>

<script>
const S = (id)=>document.getElementById(id);

// ------- STATE -------
let selectedCrop = null;
let selectedVariety = null;
let soilsForCrop = [];

// ------- TABS -------
function selectTab(name){
  S('panelTitle').innerText =
    name==='harvest' ? 'Harvesting Analysis' :
    name==='market'  ? 'Market Analysis'     :
    name==='gov'     ? 'Gov Helps'           :
    name==='aifarmer'? 'AI Farmer'           : 'Crop Analysis';

  ['panelCrop','panelMarket','panelGov','panelAI','panelHarvest']
    .forEach(id=>S(id).style.display='none');
  if(name==='harvest') S('panelHarvest').style.display='block';
  else if(name==='market') S('panelMarket').style.display='block';
  else if(name==='gov') S('panelGov').style.display='block';
  else if(name==='aifarmer') S('panelAI').style.display='block';
  else S('panelCrop').style.display='block';
}

// ------- WEATHER + tiny sparklines -------
let tempPts=[], pressPts=[];
function drawSpark(id, arr){
  const ctx = S(id).getContext('2d');
  const w = S(id).width, h=S(id).height;
  ctx.clearRect(0,0,w,h);
  if(arr.length<2) return;
  const min = Math.min(...arr), max=Math.max(...arr);
  ctx.beginPath();
  arr.forEach((v,i)=>{
    const x = i*(w/(arr.length-1));
    const y = h - ((v-min)/(max-min+1e-6))*h;
    i?ctx.lineTo(x,y):ctx.moveTo(x,y);
  });
  ctx.stroke();
}

async function refreshWeather(){
  try{
    const r = await fetch('/api/weather');
    const js = await r.json();
    S('weatherBox').innerText = `${js.city||''}  ${js.temp}°C | ${js.pressure} hPa | ${js.condition}`;
    tempPts.push(js.temp); if(tempPts.length>30) tempPts.shift();
    pressPts.push(js.pressure); if(pressPts.length>30) pressPts.shift();
    drawSpark('tempSpark', tempPts);
    drawSpark('pressSpark', pressPts);
  }catch(e){}
}
setInterval(refreshWeather, 10000);
refreshWeather();

// ------- QUICK PRICE + TRENDS -------
async function loadQuickBox(){
  try{
    const r = await fetch('/api/random_price'); const js = await r.json();
    S('qpBox').innerHTML = `
      <div><b>${js.commodity||'-'}</b></div>
      <div>${js.district||'-'} | ${js.state||'-'}</div>
      <div>Modal: ₹${js.modal_price||'-'} (per quintal)</div>
      <div style="font-size:12px;color:#777">Date: ${js.arrival_date||'-'}</div>`;
  }catch(e){ S('qpBox').innerText='—'; }

  // quick trends (Tamil+English)
  try{
    const pr = {prompt: "Give 3 short bullet points on latest agriculture trends in India (max 40 words, no numbering). Tamil+English mix, crisp."};
    const r2 = await fetch('/api/gemini', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pr)});
    const j2 = await r2.json();
    S('trends').innerText = (j2.text||'—') + (j2.provider?`  \n[${j2.provider}]`:'');
  }catch(e){ S('trends').innerText='—'; }
}
loadQuickBox();

// ------- SAVED LIST -------
async function loadSavedList(){
  const r=await fetch('/api/saved_list'); const js=await r.json();
  const box=S('savedList'); box.innerHTML='';
  js.folders.forEach(name=>{
    const d=document.createElement('div');
    d.style.display='flex'; d.style.justifyContent='space-between'; d.style.alignItems='center'; d.style.padding='6px 0';
    d.innerHTML=`<div>${name.split('-')[0]}<div style="font-size:12px;color:#777">${name.split('-').slice(1).join('-')}</div></div>`;
    const b=document.createElement('button'); b.className='btn secondary'; b.textContent='Open';
    b.onclick=()=>openSaved(name);
    d.appendChild(b);
    box.appendChild(d);
  });
}
async function openSaved(name){
  const r=await fetch('/api/load_saved?folder='+encodeURIComponent(name));
  const js=await r.json();
  S('selBlock').innerText = `${js.crop||'-'} (${js.variety||'-'})\nArea: ${js.area||'-'} ${js.unit||''}\nSoil: ${js.soil||'-'}\nSeed: ${js.seed_date||'-'}`;
  toast('Loaded: '+name);
}
loadSavedList();

// ------- CROP FLOW -------
function startCropFlow(){
  S('varietyList').style.display='none';
  S('cropList').style.display='grid';
  S('cropList').innerHTML='Loading crops…';
  // Ask AI for 60-90 crops (fallback to a large static list)
  ask('/api/gemini', {prompt:"List 80 common Indian crops as JSON array of objects with fields: en, ta. Keep each name concise."})
  .then(j=>{
    let arr=[];
    try{ arr = JSON.parse(j.text); }catch(_){}
    if(!Array.isArray(arr) || arr.length<8){
      arr = [
        {"en":"Rice","ta":"तांदूळ"},{"en":"Wheat","ta":"गहू"},{"en":"Paddy","ta":"धान"},
        {"en":"Maize","ta":"कॉर्न"},{"en":"Bajra (Pearl millet)","ta":"राई"},
        {"en":"Jowar (Sorghum)","ta":"कॉर्न (ज्वारी)"},{"en":"Ragi (Finger millet)","ta":"बातम्या"},
        {"en":"Barley","ta":"रस"},{"en":"Sugarcane","ta":"ऊस"},{"en":"Cotton","ta":"कापूस"},
        {"en":"Groundnut","ta":"शेंगदाणे"},{"en":"Mustard","ta":"मोहरी"},{"en":"Sesame","ta":"तीळ"},
        {"en":"Sunflower","ta":"सूर्यफूल"},{"en":"Soybean","ta":"सोयाबीन"},{"en":"Castor","ta":"एरंडेल तेल"},
        {"en":"Tea","ta":"चहा"},{"en":"Coffee","ta":"कॉफी"},{"en":"Rubber","ta":"रबर"},
        {"en":"Coconut","ta":"नारळ"},{"en":"Arecanut","ta":"कुरकुरीत"},{"en":"Banana","ta":"केळी"},
        {"en":"Mango","ta":"आंबा"},{"en":"Guava","ta":"पेरू"},{"en":"Papaya","ta":"पपई"},
        {"en":"Pomegranate","ta":"डाळिंब"},{"en":"Grapes","ta":"द्राक्षे"},{"en":"Apple","ta":"सफरचंद"},
        {"en":"Pineapple","ta":"अननस"},{"en":"Orange","ta":"संत्रा"},{"en":"Lemon","ta":"लिंबू"},
        {"en":"Sweet Lime","ta":"साठीकुडी"},{"en":"Litchi","ta":"लीची"},{"en":"Jackfruit","ta":"फणस"},
        {"en":"Onion","ta":"कांदा"},{"en":"Garlic","ta":"लसूण"},{"en":"Potato","ta":"बटाटे"},
        {"en":"Tomato","ta":"तमतर"},{"en":"Brinjal (Eggplant)","ta":"वांगे"},
        {"en":"Okra (Lady’s finger)","ta":"मेथी"},{"en":"Chillies","ta":"मिरची"},
        {"en":"Capsicum","ta":"मिरची"},{"en":"Cabbage","ta":"कोबी"},{"en":"Cauliflower","ta":"फुलकोबी"},
        {"en":"Peas","ta":"वाटाणे"},{"en":"French Beans","ta":"बीन्स"},{"en":"Bitter Gourd","ta":"कॅन्टलूप"},
        {"en":"Bottle Gourd","ta":"झुचिनी"},{"en":"Ridge Gourd","ta":"बिरकंकाई"},{"en":"Pumpkin","ta":"भोपळा"},
        {"en":"Coriander","ta":"धणे"},{"en":"Cumin","ta":"जिरे"},{"en":"Turmeric","ta":"पिवळा"},
        {"en":"Ginger","ta":"आले"},{"en":"Cardamom","ta":"वेलची"},{"en":"Black Pepper","ta":"मिरपूड"},
        {"en":"Clove","ta":"लवंग"},{"en":"Nutmeg","ta":"जायफळ"},{"en":"Fennel","ta":"बडीशेप"},
        {"en":"Fenugreek","ta":"मेथी"},{"en":"Bengal Gram (Chana)","ta":"चणे"},
        {"en":"Green Gram (Moong)","ta":"भूक"},{"en":"Black Gram (Urad)","ta":"चणे"},
        {"en":"Pigeon Pea (Tur)","ta":"ढोले डाळ"},{"en":"Horse Gram","ta":"मिळवा"},
        {"en":"Masoor (Red Lentil)","ta":"मसूर डाळ"},{"en":"Field Pea","ta":"उलंटांगोंडाई"},
        {"en":"Linseed (Flax)","ta":"जलबी"},{"en":"Safflower","ta":"केशर"},{"en":"Tobacco","ta":"तंबाखू"},
        {"en":"Jute","ta":"भांग"},{"en":"Hemp","ta":"अँजी"},{"en":"Kenaf","ta":"भांग"},
        {"en":"Curry Leaf","ta":"कॅरवे"},{"en":"Drumstick (Moringa)","ta":"ड्रमस्टिक"},
        {"en":"Cucumber","ta":"काकडी"},{"en":"Watermelon","ta":"टरबूज"},
        {"en":"Muskmelon","ta":"खरबूज"},{"en":"Amla (Gooseberry)","ta":"गुसबेरी"},
        {"en":"Sapota (Chikoo)","ta":"सापोटा"},{"en":"Pear","ta":"नाशपाती"},{"en":"Strawberry","ta":"स्ट्रॉबेरी"},
        {"en":"Custard Apple","ta":"चित्ता"},{"en":"Betel Leaf","ta":"सुपारी"},{"en":"Tamarind","ta":"चिंच"}
      ];
    }
    const grid=S('cropList'); grid.innerHTML='';
    arr.slice(0,120).forEach(c=>{
      const d=document.createElement('div'); d.className='item';
      d.innerHTML=`<b>${c.en}</b><div style="font-size:12px;color:#777">${c.ta||''}</div>`;
      d.onclick=()=>selectCrop(c.en);
      grid.appendChild(d);
    });
  });
}

async function selectCrop(name){
  selectedCrop=name; selectedVariety=null;
  S('varietyList').style.display='grid';
  S('varietyList').innerHTML='Loading varieties…';
  const r = await fetch(`/api/varieties?crop=${encodeURIComponent(name)}`);
  const js=await r.json();
  const list=js.varieties||[];
  const box=S('varietyList'); box.innerHTML='';
  if(list.length===0){
    const d=document.createElement('div'); d.className='item'; d.textContent='Generic';
    d.onclick=()=>selectVariety('Generic'); box.appendChild(d);
  }else{
    list.forEach(v=>{
      const d=document.createElement('div'); d.className='item'; d.textContent=v||'—';
      d.onclick=()=>selectVariety(v||'Generic'); box.appendChild(d);
    });
  }
}

function selectVariety(v){
  selectedVariety=v;
  // soil via AI (fallback static)
  ask('/api/gemini', {prompt:`Give 5 suitable soil types (short words only) for crop "${selectedCrop}" variety "${selectedVariety}" as JSON array of strings.`})
  .then(j=>{
    let arr=[]; try{arr=JSON.parse(j.text)}catch(_){}
    if(!Array.isArray(arr)||arr.length<2){ arr=['Alluvial','Black','Loamy','Clay','Sandy loam']; }
    soilsForCrop = arr;
    openPop();
  });
}

// ------- POPUP -------
function openPop(){
  S('popup').style.display='flex';
  S('popTitle').innerText = `${selectedCrop} — ${selectedVariety}`;
  const sel=S('inSoil'); sel.innerHTML='';
  soilsForCrop.forEach(s=>{
    const o=document.createElement('option'); o.value=s; o.textContent=s; sel.appendChild(o);
  });
  S('inSeed').value = new Date().toISOString().slice(0,10);
}
function closePop(){ S('popup').style.display='none'; }

async function saveCrop(){
  const obj={
    crop:selectedCrop,
    variety:selectedVariety,
    area:S('inArea').value,
    unit:S('inUnit').value,
    soil:S('inSoil').value,
    seed_date:S('inSeed').value,
    created_at:new Date().toISOString()
  };
  const r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});
  const js=await r.json();
  if(js.saved){ toast('Saved ✓'); closePop(); loadSavedList();
    S('selBlock').innerText = `${obj.crop} (${obj.variety})\nArea: ${obj.area} ${obj.unit}\nSoil: ${obj.soil}\nSeed: ${obj.seed_date}`;
  }else toast('Save failed');
}

// ------- MARKET -------
let mkChart=null;
async function runMarket(){
  const crop=S('mkCrop').value.trim();
  if(!crop){ toast('Enter crop'); return; }
  const variety=S('mkVariety').value.trim();
  const state=S('mkState').value.trim();

  const url=`/api/price_stats?crop=${encodeURIComponent(crop)}&variety=${encodeURIComponent(variety)}&state=${encodeURIComponent(state)}`;
  const r=await fetch(url); const js=await r.json();
  S('mkResult').innerHTML = `
    <div><b>Date:</b> ${js.date||'-'}</div>
    <div><b>Min:</b> ${fmt(js.min_perkg)} ₹/kg | <b>Max:</b> ${fmt(js.max_perkg)} ₹/kg | <b>Median(modal):</b> ${fmt(js.median_modal_perkg)} ₹/kg</div>
    <div><b>Samples:</b> ${js.sample_count||0}</div>`;

  const labels=(js.district_series||[]).map(x=>x.district);
  const data=(js.district_series||[]).map(x=>x.avg_modal_perkg);
  if(mkChart) mkChart.destroy();
  const ctx=S('mkChart').getContext('2d');
  mkChart = new Chart(ctx, {
    type:'line',
    data:{ labels, datasets:[{ label:'Avg modal ₹/kg', data, tension:.3 }] },
    options:{ responsive:true, scales:{ y:{ beginAtZero:false } } }
  });
}
function fmt(x){ return (x==null) ? '-' : (Math.round(x*100)/100); }

// ------- GOV / AI FARMER -------
async function askGeminiGov(){
  const q=S('govQ').value.trim(); if(!q) return;
  const j = await ask('/api/gemini',{prompt:`Answer shortly in Tamil + English mix (2-4 bullets). Topic: Government schemes for farmers in India.\nQ: ${q}`});
  S('govA').innerText = (j.text || '—') + (j.provider?`  \n[${j.provider}]`:'');
}
async function askGeminiAI(){
  const folder=S('aiFolder').value.trim();
  const q=S('aiQ').value.trim();
  if(!folder||!q){ toast('Provide folder & question'); return; }
  try{
    const det = await (await fetch('/api/load_saved?folder='+encodeURIComponent(folder))).json();
    const prompt = `You are an agronomy copilot. Using this saved crop JSON and the user question, reply concisely in Tamil + English.\nJSON:\n${JSON.stringify(det)}\n\nQ: ${q}`;
    const j = await ask('/api/gemini',{prompt});
    S('aiA').innerText = (j.text || '—') + (j.provider?`  \n[${j.provider}]`:'');
  }catch(e){ S('aiA').innerText='—'; }
}
async function runHarvest(){
  const folder=S('hvFolder').value.trim(); if(!folder){ toast('Enter folder'); return; }
  try{
    const det = await (await fetch('/api/load_saved?folder='+encodeURIComponent(folder))).json();
    const prompt = `For this crop, estimate harvest date & season (Kharif/Rabi/Zaid), list key risks & possible delays with reasons, compact bullets. Reply in Tamil + English.\nJSON:\n${JSON.stringify(det)}`;
    const j = await ask('/api/gemini',{prompt});
    S('hvOut').innerText = (j.text || '—') + (j.provider?`  \n[${j.provider}]`:'');
  }catch(e){ S('hvOut').innerText='—'; }
}

// ------- FAB CHAT -------
S('fab').onclick=()=>{
  const p=S('fabPanel');
  p.style.display = (p.style.display==='block')?'none':'block';
}
async function askMini(){
  const q=S('miniQ').value.trim(); if(!q) return;
  const j = await ask('/api/gemini',{prompt:`Short helpful answer (Tamil+English): ${q}`});
  S('miniA').innerText = (j.text || '—') + (j.provider?`  \n[${j.provider}]`:'');
}

// ------- UTIL -------
async function ask(url, body){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return await r.json();
}
function toast(msg){
  const t=S('toast'); t.innerText=msg; t.style.display='block';
  setTimeout(()=>t.style.display='none', 2500);
}

// Initial
selectTab('crop');
</script>
</body>
</html>
"""

# ----------- MAIN -----------
if __name__ == "__main__":
    print("FieldLink single-file app starting on http://127.0.0.1:5000")
    # Auto-open browser a moment after server starts
    threading.Timer(0.8, _open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=True)
