import httpx
import json
import time
from collections import defaultdict
from flask import Flask, request, jsonify, Response
from datetime import datetime

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════
# 🔑 CHANGE PASSWORD HERE ONLY (SINGLE SOURCE OF TRUTH)
# ═══════════════════════════════════════════════════════════════
API_PASSWORD = "crystalapi"

# ═══════════════════════════════════════════════════════════════
# 🛡️ RATE LIMIT CONFIG (per IP)
# ═══════════════════════════════════════════════════════════════
RATE_LIMIT_REQUESTS = 30        # max requests
RATE_LIMIT_WINDOW   = 60        # in seconds
_rate_bucket = defaultdict(list)  # { ip: [timestamps...] }

DEVICE_ID = "4e93e5106b39e1902e24d1ba2f17c709"


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def check_password():
    provided = request.args.get("password") or request.headers.get("X-API-Password")
    return provided == API_PASSWORD


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limited():
    ip = client_ip()
    now = time.time()
    bucket = _rate_bucket[ip]
    _rate_bucket[ip] = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_bucket[ip]) >= RATE_LIMIT_REQUESTS:
        return True
    _rate_bucket[ip].append(now)
    return False


def convert_timestamp(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Invalid timestamp"


def build_map_info_payload(data):
    return {
        "map_info": {
            "workshop_code": data["data"]["workshop_code_info"]["workshop_code"],
            "author_name": data["data"]["workshop_code_info"]["author_name"],
            "map_name": data["data"]["workshop_code_info"]["workshop_name"],
            "description": data["data"]["workshop_code_info"]["workshop_desc"],
            "team_count": data["data"]["workshop_code_info"]["team_count"],
            "game_mode": data["data"]["workshop_code_info"]["game_mode"],
            "subscribe_count": data["data"]["workshop_code_info"]["subscribe_count"],
            "like_count": data["data"]["workshop_code_info"]["like_count"],
            "estimated_play_time": f"{data['data']['workshop_code_info']['min_est_play_time']} seconds",
            "tags": data["data"]["workshop_code_info"]["tags"],
        },
        "game_info": {
            "title": data["data"]["title"],
            "game_name": data["data"]["game_name"],
            "region": data["data"]["region"],
            "language": data["data"]["lang"],
            "android_download": data["data"]["android_download_url"],
            "ios_download": data["data"]["ios_download_url"],
            "ugc_url": data["data"]["ugc_url"],
        },
        "images": {
            "backgrounds": data["data"]["imgs"],
            "game_icon": data["data"]["game_icon"],
            "share_image": data["data"]["share_img"],
        },
        "timestamps": {
            "start_time": data["data"]["start_time"],
            "end_time": data["data"]["end_time"],
            "start_time_formatted": convert_timestamp(data["data"]["start_time"]),
            "end_time_formatted": convert_timestamp(data["data"]["end_time"]),
        },
    }


def fetch_ff_map(map_code, region, lang):
    if map_code.startswith("#"):
        map_code = map_code[1:]

    encoded = f"%23{map_code}"
    url = (
        f"https://mapshare.freefiremobile.com/api/info"
        f"?lang={lang}&region={region}&map_code={encoded}&device_id={DEVICE_ID}"
    )

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url)
    except httpx.RequestError as e:
        return None, (503, "Network error", str(e))
    except Exception as e:
        return None, (500, "Server error", str(e))

    if r.status_code != 200:
        return None, (r.status_code, "API error", f"Free Fire returned {r.status_code}")

    try:
        data = r.json()
    except json.JSONDecodeError:
        return None, (500, "Bad JSON", "Invalid response from Free Fire")

    if data.get("code") != 0:
        return None, (data.get("code", 500), data.get("msg", "Unknown error"), None)

    return data, None


# ═══════════════════════════════════════════════════════════════
# 🌐 BASE ROUTE — WEBSITE
# ═══════════════════════════════════════════════════════════════
@app.route('/', methods=['GET'])
def home():
    return Response(HTML_PAGE, mimetype='text/html')


# ═══════════════════════════════════════════════════════════════
# 🌉 /public_info — PUBLIC BRIDGE FOR WEBSITE (NO PASSWORD, ANY DOMAIN)
# ═══════════════════════════════════════════════════════════════
@app.route('/public_info', methods=['GET'])
def public_info():
    if rate_limited():
        return jsonify({
            "code": 429, "status": "error",
            "msg": f"Too many requests. Try again in {RATE_LIMIT_WINDOW}s.",
            "data": None
        }), 429

    map_code = request.args.get('map_code')
    region = request.args.get('region', 'IND').upper()
    lang = request.args.get('lang', 'en').lower()

    if not map_code:
        return jsonify({
            "code": 400, "status": "error",
            "msg": "map_code is required", "data": None
        }), 400

    data, err = fetch_ff_map(map_code, region, lang)

    if err:
        code, short, detail = err
        return jsonify({
            "code": code, "status": "error",
            "msg": f"{short}: {detail}" if detail else short,
            "data": None
        }), code

    return jsonify({
        "code": 0, "status": "success", "msg": "",
        "data": build_map_info_payload(data)
    })


# ═══════════════════════════════════════════════════════════════
# 🔒 /info — PASSWORD PROTECTED (For API Users)
# ═══════════════════════════════════════════════════════════════
@app.route('/info', methods=['GET'])
def get_map_info():
    if not check_password():
        return jsonify({
            "code": 401, "status": "error",
            "msg": "Unauthorized. Invalid or missing password.",
            "data": None
        }), 401

    map_code = request.args.get('map_code')
    region = request.args.get('region', 'IND').upper()
    lang = request.args.get('lang', 'en').lower()

    if not map_code:
        return jsonify({
            "code": 400, "status": "error",
            "msg": "map_code is required", "data": None
        }), 400

    data, err = fetch_ff_map(map_code, region, lang)

    if err:
        code, short, detail = err
        return jsonify({
            "code": code, "status": "error",
            "msg": f"{short}: {detail}" if detail else short,
            "data": None
        }), code

    return jsonify({
        "code": 0, "status": "success", "msg": "",
        "data": build_map_info_payload(data)
    })


# ═══════════════════════════════════════════════════════════════
# 🔒 /map_details — PASSWORD PROTECTED (Detailed)
# ═══════════════════════════════════════════════════════════════
@app.route('/map_details', methods=['GET'])
def get_map_details():
    if not check_password():
        return jsonify({
            "code": 401, "status": "error",
            "msg": "Unauthorized. Invalid or missing password."
        }), 401

    map_code = request.args.get('map_code')
    region = request.args.get('region', 'IND').upper()
    lang = request.args.get('lang', 'en').lower()

    if not map_code:
        return jsonify({"error": "map_code is required"}), 400

    data, err = fetch_ff_map(map_code, region, lang)

    if err:
        code, short, detail = err
        return jsonify({
            "code": code, "status": "error",
            "msg": f"{short}: {detail}" if detail else short
        }), code

    workshop_info = data["data"]["workshop_code_info"]
    game_config   = data["data"]["game_config"]

    tags_with_names = []
    for tag_id in workshop_info.get("tags", []):
        tag_info = next(
            (t for t in game_config["show_tag_options_flat"] if t["id"] == tag_id),
            None
        )
        if tag_info:
            tag_name = game_config["translations"].get(
                tag_info["tag_key"], tag_info["tag_key"]
            )
            tags_with_names.append({
                "id": tag_id,
                "key": tag_info["tag_key"],
                "name": tag_name,
                "type": tag_info["tag_type"],
            })

    game_mode_id = workshop_info.get("game_mode")
    game_mode_name = game_config["translations"].get(
        game_config["game_mode_id_key_map"].get(
            str(game_mode_id), f"Unknown Mode {game_mode_id}"
        )
    )

    mode_template_id = workshop_info.get("mode_template_id")
    mode_template_name = game_config["translations"].get(
        game_config["mode_template_id_key_map"].get(
            str(mode_template_id), f"Unknown Template {mode_template_id}"
        )
    )

    return jsonify({
        "code": 0, "status": "success",
        "data": {
            "basic_info": {
                "workshop_code": workshop_info["workshop_code"],
                "map_name": workshop_info["workshop_name"],
                "author": workshop_info["author_name"],
                "description": workshop_info["workshop_desc"],
                "short_description": workshop_info.get("sub_desc", ""),
            },
            "gameplay_info": {
                "team_count": workshop_info["team_count"],
                "group_mode": workshop_info["group_mode"],
                "game_mode": {"id": game_mode_id, "name": game_mode_name},
                "mode_template": {"id": mode_template_id, "name": mode_template_name},
                "round_count": workshop_info.get("round_count", 1),
                "map_id": workshop_info.get("map_id"),
                "estimated_play_time": (
                    f"{workshop_info['min_est_play_time']} - "
                    f"{workshop_info['max_est_play_time']} seconds"
                ),
            },
            "social_info": {
                "subscribe_count": workshop_info["subscribe_count"],
                "like_count": workshop_info["like_count"],
                "map_cover_url": workshop_info.get("map_cover_url", ""),
            },
            "tags": tags_with_names,
            "download_info": {
                "android": data["data"]["android_download_url"],
                "ios": data["data"]["ios_download_url"],
                "ugc_portal": data["data"]["ugc_url"],
            },
            "region_info": {
                "region": data["data"]["region"],
                "language": data["data"]["lang"],
                "region_lang": data["data"]["region_lang"],
            },
        }
    })


# ═══════════════════════════════════════════════════════════════
# 🩺 HEALTH
# ═══════════════════════════════════════════════════════════════
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "running",
        "service": "Crystal APIs - Free Fire Map Share",
        "developer": "@srking5306",
        "credits": "@f1api",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "/":             "Website Interface (public)",
            "/public_info":  "Website bridge (public, rate-limited)",
            "/info":         "Basic map info (password required)",
            "/map_details":  "Detailed map info (password required)",
            "/health":       "Health check",
        }
    })


# ═══════════════════════════════════════════════════════════════
# 🎨 WEBSITE HTML (embedded)
# ═══════════════════════════════════════════════════════════════
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0F0F23">
<title>Crystal APIs • Free Fire Map Info</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=Russo+One&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<script>document.documentElement.className='js'</script>
<style>
:root{
  --bg:#0F0F23;--card:rgba(30,28,53,.62);--solid:#16142e;--mut2:#27273B;
  --txt:#E2E8F0;--mut:#A9A5D3;--bd:rgba(124,58,237,.28);--bdh:rgba(167,139,250,.6);
  --p:#7C3AED;--p2:#A78BFA;--acc:#F43F5E;--cy:#22D3EE;--ok:#34D399;
  --ease:cubic-bezier(.16,1,.3,1);
  --h:'Russo One','Chakra Petch',sans-serif;--m:'JetBrains Mono',ui-monospace,monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{font-family:'Chakra Petch',system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;overflow-x:hidden;line-height:1.55;font-size:16px}
a{color:inherit}
:focus-visible{outline:2px solid var(--cy);outline-offset:3px;border-radius:10px}
.ic{width:1.1em;height:1.1em;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}

/* ---------- background (GPU only) ---------- */
#fx{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.orb{position:fixed;z-index:0;border-radius:50%;pointer-events:none;will-change:transform;animation:drift 22s ease-in-out infinite}
.o1{width:640px;height:640px;top:-240px;left:-200px;background:radial-gradient(circle,rgba(124,58,237,.5),transparent 68%)}
.o2{width:700px;height:700px;bottom:-300px;right:-220px;background:radial-gradient(circle,rgba(34,211,238,.28),transparent 68%);animation-delay:-8s}
.o3{width:480px;height:480px;top:38%;left:58%;background:radial-gradient(circle,rgba(244,63,94,.24),transparent 68%);animation-delay:-14s}
@keyframes drift{0%,100%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(60px,-40px,0) scale(1.12)}}
.floor{position:fixed;left:-50%;right:-50%;bottom:0;height:55vh;z-index:0;pointer-events:none;overflow:hidden;
  -webkit-mask-image:linear-gradient(to top,#000,transparent);mask-image:linear-gradient(to top,#000,transparent);opacity:.55}
.floor::before{content:"";position:absolute;left:0;right:0;top:-60px;bottom:0;
  background:linear-gradient(rgba(167,139,250,.35) 1px,transparent 1px),linear-gradient(90deg,rgba(167,139,250,.35) 1px,transparent 1px);
  background-size:64px 64px;transform:perspective(420px) rotateX(62deg);transform-origin:50% 100%;animation:grid 3s linear infinite;will-change:transform}
@keyframes grid{to{transform:perspective(420px) rotateX(62deg) translate3d(0,64px,0)}}
#glow{position:fixed;top:0;left:0;width:500px;height:500px;z-index:0;pointer-events:none;opacity:0;transition:opacity .4s;
  background:radial-gradient(circle,rgba(124,58,237,.2),transparent 65%);will-change:transform}
#bar{position:fixed;top:0;left:0;right:0;height:3px;z-index:60;transform:scaleX(0);transform-origin:0 50%;background:linear-gradient(90deg,var(--p),var(--cy),var(--acc))}

.wrap{position:relative;z-index:2;max-width:1160px;margin:0 auto;padding:18px 20px 40px;padding-top:max(18px,env(safe-area-inset-top))}

/* ---------- header ---------- */
header{position:sticky;top:12px;z-index:50;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 14px 10px 16px;
  border:1px solid var(--bd);border-radius:20px;background:rgba(15,15,35,.72);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);animation:drop .8s var(--ease) both}
@keyframes drop{from{opacity:0;transform:translate3d(0,-24px,0)}}
.brand{display:flex;align-items:center;gap:12px;text-decoration:none}
.logo{width:40px;height:40px;filter:drop-shadow(0 0 10px rgba(124,58,237,.8))}
.brand b{font-family:var(--h);font-weight:400;font-size:19px;letter-spacing:.5px;display:block;line-height:1.1}
.brand b i{font-style:normal;background:linear-gradient(90deg,var(--p2),var(--cy));-webkit-background-clip:text;background-clip:text;color:transparent}
.brand small{font-size:10.5px;letter-spacing:2px;color:var(--mut);text-transform:uppercase}
nav{display:flex;gap:4px}
nav a{padding:10px 16px;border-radius:12px;font-weight:600;font-size:14px;color:var(--mut);text-decoration:none;transition:color .2s,background-color .2s}
nav a:hover{color:#fff;background:rgba(124,58,237,.18)}
.pill{display:flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:1px;color:var(--ok);background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.35)}
.pill i{width:8px;height:8px;border-radius:50%;background:var(--ok);position:relative}
.pill i::after{content:"";position:absolute;inset:0;border-radius:50%;background:var(--ok);animation:ping 1.8s ease-out infinite}
@keyframes ping{to{transform:scale(3.2);opacity:0}}
@media(max-width:760px){nav{display:none}.brand small{display:none}}

/* ---------- hero ---------- */
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:20px;align-items:center;padding:56px 0 34px}
.eyebrow{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:var(--p2);background:rgba(124,58,237,.12);border:1px solid var(--bd)}
h1{font-family:var(--h);font-weight:400;font-size:clamp(38px,6.4vw,74px);line-height:1.02;letter-spacing:-1px;margin:18px 0 16px;perspective:600px}
.w{display:inline-block;animation:rise .9s var(--ease) both;animation-delay:calc(var(--i)*90ms + 150ms);transform-origin:50% 100%}
@keyframes rise{from{opacity:0;transform:translate3d(0,36px,0) rotateX(-50deg)}}
.g{background:linear-gradient(110deg,var(--p2),var(--cy) 55%,var(--acc));-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{color:var(--mut);font-size:17px;max-width:540px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:28px}
.stat{min-width:104px;padding:14px 18px;border-radius:16px;background:var(--card);border:1px solid var(--bd)}
.stat b{display:block;font-family:var(--h);font-weight:400;font-size:28px;line-height:1.1;color:#fff}
.stat span{font-size:11px;letter-spacing:1.6px;text-transform:uppercase;color:var(--mut)}
.gem{position:relative;display:grid;place-items:center;min-height:320px;animation:fadein 1.2s .3s both}
@keyframes fadein{from{opacity:0;transform:scale(.8)}}
.gem svg.cr{width:min(240px,60%);position:relative;z-index:2;filter:drop-shadow(0 0 34px rgba(124,58,237,.75));animation:float 5s ease-in-out infinite;will-change:transform}
@keyframes float{50%{transform:translate3d(0,-18px,0) rotate(2deg)}}
.ring{position:absolute;border-radius:50%;border:1.5px dashed rgba(167,139,250,.45);animation:rot 24s linear infinite;will-change:transform}
.ring.a{width:300px;height:300px}.ring.b{width:380px;height:380px;border-color:rgba(34,211,238,.3);animation-duration:36s;animation-direction:reverse}
.ring::after{content:"";position:absolute;top:-5px;left:50%;width:10px;height:10px;margin-left:-5px;border-radius:50%;background:var(--cy);box-shadow:0 0 14px var(--cy)}
@keyframes rot{to{transform:rotate(360deg)}}
.spark{position:absolute;width:6px;height:6px;background:#fff;border-radius:50%;box-shadow:0 0 12px #fff;animation:tw 2.4s ease-in-out infinite}
@keyframes tw{0%,100%{opacity:0;transform:scale(.3)}50%{opacity:1;transform:scale(1)}}
@media(max-width:860px){.hero{grid-template-columns:1fr;text-align:center;padding-top:36px}.lead{margin:0 auto}.stats{justify-content:center}.gem{order:-1;min-height:260px}.ring.a{width:240px;height:240px}.ring.b{width:300px;height:300px}}

/* ---------- search ---------- */
.sc{position:relative;border-radius:24px;padding:2px;overflow:hidden;isolation:isolate;margin-bottom:26px}
.sc::before{content:"";position:absolute;inset:-70%;z-index:-2;background:conic-gradient(from 0deg,transparent 0 62%,var(--p),var(--cy),var(--acc),transparent);animation:rot 7s linear infinite;will-change:transform}
.sc .in{background:var(--solid);border-radius:22px;padding:22px}
.row{display:grid;grid-template-columns:1fr 190px auto;gap:12px}
.fld label{display:block;font-size:11px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:var(--mut);margin:0 0 7px 4px}
.fld .box{position:relative}
.fld .ic{position:absolute;left:16px;top:50%;margin-top:-.55em;color:var(--p2);pointer-events:none}
input,select{width:100%;height:54px;padding:0 16px 0 46px;border-radius:15px;font:600 15px var(--m);color:var(--txt);background:#0c0b1e;border:1px solid var(--bd);outline:none;transition:border-color .2s,box-shadow .2s}
select{padding-left:16px;font-family:'Chakra Petch',sans-serif;cursor:pointer}
input:focus,select:focus{border-color:var(--bdh);box-shadow:0 0 0 4px rgba(124,58,237,.22)}
input::placeholder{color:#6b6696;font-weight:400}
select option{background:#0c0b1e}
.btn{position:relative;overflow:hidden;height:54px;padding:0 28px;align-self:end;border:0;border-radius:15px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:10px;
  font:700 15px 'Chakra Petch',sans-serif;letter-spacing:.6px;color:#fff;background:linear-gradient(135deg,var(--p),#C026D3 55%,var(--acc));
  box-shadow:0 12px 30px -10px rgba(124,58,237,.9);transition:transform .25s var(--ease),box-shadow .25s}
.btn::after{content:"";position:absolute;top:0;left:0;width:60%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.4),transparent);transform:translate3d(-130%,0,0) skewX(-20deg);transition:transform .7s var(--ease)}
.btn:hover{transform:translate3d(0,-3px,0);box-shadow:0 18px 38px -10px rgba(244,63,94,.7)}
.btn:hover::after{transform:translate3d(220%,0,0) skewX(-20deg)}
.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.7;cursor:wait}
.sp{display:none;width:16px;height:16px;border-radius:50%;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;animation:rot .7s linear infinite}
.btn.ld .sp{display:block}.btn.ld .ic{display:none}
.hint{margin:12px 4px 0;font-size:12.5px;color:var(--mut)}
kbd{font:600 11px var(--m);padding:2px 7px;border-radius:6px;border:1px solid var(--bd);background:var(--mut2)}
@media(max-width:720px){.row{grid-template-columns:1fr}}

/* ---------- result ---------- */
#result{margin-bottom:26px}
.card{position:relative;background:var(--card);border:1px solid var(--bd);border-radius:24px;padding:26px;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);box-shadow:0 30px 80px -40px rgba(124,58,237,.6)}
.hidden{display:none}
.rv{will-change:opacity,transform}
.js .rv{opacity:0;transform:translate3d(0,26px,0);transition:opacity .7s var(--ease),transform .7s var(--ease);transition-delay:calc(var(--i,0)*70ms)}
.js .rv.in{opacity:1;transform:none;will-change:auto}
.js .tilt.rv.in{transform:perspective(800px) rotateX(var(--rx,0deg)) rotateY(var(--ry,0deg));will-change:transform;transition:opacity .7s var(--ease),transform .25s var(--ease);transition-delay:calc(var(--i,0)*70ms),0s}
.sk{border-radius:14px;background:var(--mut2);position:relative;overflow:hidden}
.sk::after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent,rgba(167,139,250,.22),transparent);animation:shim 1.3s infinite;transform:translate3d(-100%,0,0)}
@keyframes shim{to{transform:translate3d(100%,0,0)}}
.mh{display:grid;grid-template-columns:auto 1fr;gap:22px;align-items:center}
.thumb{width:132px;height:132px;border-radius:20px;object-fit:cover;background:var(--mut2);border:1px solid var(--bdh);box-shadow:0 0 40px rgba(124,58,237,.45)}
.tilt{transform:perspective(800px) rotateX(var(--rx,0deg)) rotateY(var(--ry,0deg));transition:transform .25s var(--ease);will-change:transform}
.mh h3{font-family:var(--h);font-weight:400;font-size:clamp(22px,3.4vw,32px);line-height:1.15;margin-bottom:6px}
.by{font-size:14px;color:var(--mut)}.by b{color:var(--p2)}
.desc{margin-top:10px;color:#C9C5EA;font-size:15px}
@media(max-width:640px){.mh{grid-template-columns:1fr;text-align:center}.thumb{margin:0 auto}}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
.chip{display:flex;align-items:center;gap:9px;padding:10px 16px;border-radius:14px;background:#0c0b1e;border:1px solid var(--bd);font-weight:700;font-size:14px}
.chip .ic{color:var(--cy)}
.tags{margin-top:16px;display:flex;gap:8px;flex-wrap:wrap}
.tag{padding:6px 14px;border-radius:999px;font-size:12px;font-weight:700;color:#DDD6FE;background:rgba(124,58,237,.16);border:1px solid var(--bd)}
.st{display:flex;align-items:center;gap:12px;margin:28px 0 12px;font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--mut);font-weight:700}
.st::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--bd),transparent)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.tile{position:relative;padding:14px 16px;border-radius:16px;background:#0c0b1e;border:1px solid var(--bd);transition:transform .25s var(--ease),border-color .2s}
.tile:hover{transform:translate3d(0,-3px,0);border-color:var(--bdh)}
.tile .l{font-size:10.5px;letter-spacing:1.6px;text-transform:uppercase;color:var(--mut);font-weight:700;margin-bottom:4px}
.tile .v{font-weight:700;font-size:15.5px;word-break:break-word}.tile .v.m{font-family:var(--m);font-size:13.5px}
.cp{position:absolute;top:10px;right:10px;width:34px;height:34px;display:grid;place-items:center;border-radius:10px;border:1px solid var(--bd);background:var(--mut2);color:var(--p2);cursor:pointer;transition:background-color .2s,transform .2s}
.cp:hover{background:var(--p);color:#fff}.cp:active{transform:scale(.9)}
.acts{display:flex;gap:10px;flex-wrap:wrap}
.ghost{display:inline-flex;align-items:center;gap:9px;min-height:44px;padding:0 18px;border-radius:14px;text-decoration:none;font-weight:700;font-size:14px;color:#DDD6FE;background:rgba(124,58,237,.1);border:1px solid var(--bd);cursor:pointer;font-family:inherit;transition:transform .25s var(--ease),border-color .2s,background-color .2s}
.ghost:hover{transform:translate3d(0,-3px,0);border-color:var(--bdh);background:rgba(124,58,237,.25);color:#fff}
.raw{margin-top:12px;padding:16px;border-radius:16px;background:#080817;border:1px solid var(--bd);font:12.5px/1.65 var(--m);color:#C9C5EA;max-height:380px;overflow:auto;white-space:pre-wrap;word-break:break-all;animation:fadeup .45s var(--ease)}
@keyframes fadeup{from{opacity:0;transform:translate3d(0,12px,0)}}
.alert{display:flex;align-items:center;gap:12px;padding:16px 20px;border-radius:16px;font-weight:600;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.45);color:#FCA5A5;animation:shake .5s}
@keyframes shake{20%{transform:translate3d(-8px,0,0)}40%{transform:translate3d(7px,0,0)}60%{transform:translate3d(-5px,0,0)}80%{transform:translate3d(3px,0,0)}}

/* ---------- sections ---------- */
.sec{margin-top:70px}
.sec>h2{font-family:var(--h);font-weight:400;font-size:clamp(26px,4vw,40px);text-align:center;letter-spacing:-.5px}
.sec>p{text-align:center;color:var(--mut);margin:8px auto 28px;max-width:520px}
.fg{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}
.fc{padding:24px;border-radius:22px;background:var(--card);border:1px solid var(--bd);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
.fc:hover{border-color:var(--bdh)}
.fi{width:52px;height:52px;display:grid;place-items:center;border-radius:16px;font-size:24px;color:#fff;margin-bottom:16px;background:linear-gradient(135deg,var(--p),var(--cy));box-shadow:0 10px 28px -8px rgba(124,58,237,.9)}
.fc:nth-child(2) .fi{background:linear-gradient(135deg,var(--acc),#C026D3)}.fc:nth-child(3) .fi{background:linear-gradient(135deg,var(--cy),var(--p))}.fc:nth-child(4) .fi{background:linear-gradient(135deg,var(--ok),var(--cy))}
.fc h3{font-size:18px;margin-bottom:6px}.fc p{font-size:14.5px;color:var(--mut)}
.ep{display:flex;align-items:center;gap:14px;padding:14px 16px;margin-bottom:12px;border-radius:18px;background:var(--card);border:1px solid var(--bd);transition:border-color .2s}
.ep:hover{border-color:var(--bdh)}
.ep em{font:700 11px var(--m);font-style:normal;padding:5px 9px;border-radius:8px;color:var(--ok);background:rgba(52,211,153,.12);border:1px solid rgba(52,211,153,.35)}
.ep code{flex:1;font:13px var(--m);color:#DDD6FE;overflow-x:auto;white-space:nowrap;padding-bottom:2px}
.ep small{color:var(--mut);font-size:12.5px;white-space:nowrap}
.ep .cp{position:static;flex:none}
@media(max-width:760px){.ep{flex-wrap:wrap}.ep code{order:3;flex-basis:100%}.ep small{flex:1}}
footer{margin-top:70px;padding:26px 20px;text-align:center;border:1px solid var(--bd);border-radius:22px;background:var(--card);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
footer b{font-family:var(--h);font-weight:400;font-size:18px}
.cr2{display:flex;justify-content:center;gap:22px;flex-wrap:wrap;margin-top:10px;font-size:14px;color:var(--mut)}
.cr2 a{color:var(--p2);font-weight:700;text-decoration:none;transition:color .2s}.cr2 a:hover{color:var(--cy)}
.copy{margin-top:14px;font-size:12px;color:#7a75a8;letter-spacing:.5px}
#toast{position:fixed;left:50%;bottom:28px;z-index:80;display:flex;align-items:center;gap:10px;padding:12px 20px;border-radius:14px;font-weight:700;font-size:14px;background:var(--solid);border:1px solid var(--bdh);box-shadow:0 18px 40px -12px #000;opacity:0;transform:translate3d(-50%,30px,0);transition:opacity .3s,transform .4s var(--ease);pointer-events:none}
#toast.on{opacity:1;transform:translate3d(-50%,0,0)}#toast .ic{color:var(--ok)}

@media(prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{animation:none!important;transition:none!important}
  .js .rv{opacity:1;transform:none}
}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></symbol>
  <symbol id="i-zap" viewBox="0 0 24 24"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></symbol>
  <symbol id="i-target" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></symbol>
  <symbol id="i-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/></symbol>
  <symbol id="i-unlock" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></symbol>
  <symbol id="i-heart" viewBox="0 0 24 24"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/></symbol>
  <symbol id="i-bell" viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/></symbol>
  <symbol id="i-users" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/></symbol>
  <symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></symbol>
  <symbol id="i-dl" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></symbol>
  <symbol id="i-copy" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/></symbol>
  <symbol id="i-code" viewBox="0 0 24 24"><path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/></symbol>
  <symbol id="i-ext" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3"/></symbol>
</svg>

<div id="bar"></div>
<canvas id="fx" aria-hidden="true"></canvas>
<div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div>
<div class="floor" aria-hidden="true"></div>
<div id="glow" aria-hidden="true"></div>

<div class="wrap">
<header>
  <a class="brand" href="#top" aria-label="Crystal APIs home">
    <svg class="logo" viewBox="0 0 200 240" aria-hidden="true"><use href="#gem"/></svg>
    <span><b><i>Crystal</i> APIs</b><small>Free Fire Map Intelligence</small></span>
  </a>
  <nav aria-label="Main"><a href="#search">Search</a><a href="#features">Features</a><a href="#api">API</a></nav>
  <div class="pill"><i></i>ONLINE</div>
</header>

<main id="top">
<section class="hero">
  <div>
    <span class="eyebrow"><svg class="ic"><use href="#i-zap"/></svg>Workshop Map Lookup</span>
    <h1><span class="w" style="--i:0">Explore</span> <span class="w g" style="--i:1">Free&nbsp;Fire</span> <span class="w" style="--i:2">Maps</span><br><span class="w" style="--i:3">In</span> <span class="w" style="--i:4">Real</span> <span class="w" style="--i:5">Time</span></h1>
    <p class="lead rv" style="--i:6">Paste any Free Fire Workshop map code and instantly get the author, stats, tags, images and download links — all in one clean view.</p>
    <div class="stats rv" style="--i:7">
      <div class="stat"><b data-to="10">0</b><span>Regions</span></div>
      <div class="stat"><b data-to="3">0</b><span>Endpoints</span></div>
      <div class="stat"><b data-to="100" data-suf="%">0</b><span>Free</span></div>
    </div>
  </div>
  <div class="gem" aria-hidden="true">
    <div class="ring a"></div><div class="ring b"></div>
    <span class="spark" style="top:18%;left:22%"></span><span class="spark" style="top:30%;right:16%;animation-delay:-.8s"></span><span class="spark" style="bottom:20%;left:30%;animation-delay:-1.6s"></span>
    <svg class="cr" viewBox="0 0 200 240"><defs>
      <linearGradient id="a" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C4B5FD"/><stop offset="1" stop-color="#7C3AED"/></linearGradient>
      <linearGradient id="b" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#67E8F9"/><stop offset="1" stop-color="#6D28D9"/></linearGradient>
      <linearGradient id="c" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7C3AED"/><stop offset="1" stop-color="#312E81"/></linearGradient>
      <linearGradient id="d" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F43F5E"/><stop offset="1" stop-color="#4C1D95"/></linearGradient>
      <g id="gem"><polygon points="35,65 100,10 100,65" fill="url(#a)"/><polygon points="100,10 165,65 100,65" fill="url(#b)"/><polygon points="35,65 100,65 100,230" fill="url(#c)"/><polygon points="100,65 165,65 100,230" fill="url(#d)"/><path d="M100 10v220M35 65h130" stroke="rgba(255,255,255,.35)" stroke-width="1.5" fill="none"/></g>
    </defs><use href="#gem"/></svg>
  </div>
</section>

<section id="search" class="sc rv" aria-label="Map search">
  <div class="in">
    <div class="row">
      <div class="fld"><label for="code">Workshop map code</label>
        <div class="box"><svg class="ic"><use href="#i-search"/></svg><input id="code" type="text" placeholder="e.g. 23FREEFIRE435DE8FA..." autocomplete="off" spellcheck="false" enterkeyhint="search"></div></div>
      <div class="fld"><label for="region">Region</label>
        <select id="region"><option value="IND">IND — India</option><option value="SG">SG — Singapore</option><option value="ID">ID — Indonesia</option><option value="TH">TH — Thailand</option><option value="VN">VN — Vietnam</option><option value="BR">BR — Brazil</option><option value="ME">ME — Middle East</option><option value="PK">PK — Pakistan</option><option value="BD">BD — Bangladesh</option><option value="EG">EG — Egypt</option></select></div>
      <button class="btn" id="go" type="button"><span class="sp"></span><svg class="ic"><use href="#i-zap"/></svg>Get Info</button>
    </div>
    <p class="hint">Press <kbd>/</kbd> to focus &bull; <kbd>Enter</kbd> to search</p>
  </div>
</section>

<div id="result" class="hidden" aria-live="polite"></div>

<section id="features" class="sec">
  <h2 class="rv">Built to be <span class="g">fast &amp; simple</span></h2>
  <p class="rv" style="--i:1">Everything you need to inspect a Workshop map, without the clutter.</p>
  <div class="fg">
    <div class="fc tilt rv" style="--i:0"><div class="fi"><svg class="ic"><use href="#i-zap"/></svg></div><h3>Instant Results</h3><p>Paste a code and get complete map details in a moment.</p></div>
    <div class="fc tilt rv" style="--i:1"><div class="fi"><svg class="ic"><use href="#i-target"/></svg></div><h3>Accurate Data</h3><p>Pulled straight from the official Free Fire map share service.</p></div>
    <div class="fc tilt rv" style="--i:2"><div class="fi"><svg class="ic"><use href="#i-globe"/></svg></div><h3>Multi-Region</h3><p>Switch between 10 regions with a single dropdown.</p></div>
    <div class="fc tilt rv" style="--i:3"><div class="fi"><svg class="ic"><use href="#i-unlock"/></svg></div><h3>Free Access</h3><p>No sign-up for the website. API access with a simple key.</p></div>
  </div>
</section>

<section id="api" class="sec">
  <h2 class="rv">Developer <span class="g">API</span></h2>
  <p class="rv" style="--i:1">Simple GET endpoints. Replace the placeholders with your own values.</p>
  <div class="ep rv" style="--i:0"><em>GET</em><code>/info?map_code=CODE&amp;region=IND&amp;password=YOUR_PASSWORD</code><small>Basic map info</small><button class="cp" type="button" aria-label="Copy /info URL" data-u="/info?map_code=CODE&region=IND&password=YOUR_PASSWORD"><svg class="ic"><use href="#i-copy"/></svg></button></div>
  <div class="ep rv" style="--i:1"><em>GET</em><code>/map_details?map_code=CODE&amp;region=IND&amp;password=YOUR_PASSWORD</code><small>Detailed info</small><button class="cp" type="button" aria-label="Copy /map_details URL" data-u="/map_details?map_code=CODE&region=IND&password=YOUR_PASSWORD"><svg class="ic"><use href="#i-copy"/></svg></button></div>
  <div class="ep rv" style="--i:2"><em>GET</em><code>/health</code><small>Health check</small><button class="cp" type="button" aria-label="Copy /health URL" data-u="/health"><svg class="ic"><use href="#i-copy"/></svg></button></div>
</section>
</main>

<footer class="rv">
  <b class="g">Crystal APIs</b>
  <div class="cr2"><span>Developer: <a href="https://t.me/srking5306" target="_blank" rel="noopener">@srking5306</a></span><span>Credits: <a href="https://t.me/ffapis" target="_blank" rel="noopener">@f1api</a></span></div>
  <div class="copy">&copy; 2025 Crystal APIs &bull; All Rights Reserved</div>
</footer>
</div>
<div id="toast" role="status"><svg class="ic"><use href="#i-check"/></svg><span></span></div>

<script>
const $=s=>document.querySelector(s),RM=matchMedia('(prefers-reduced-motion: reduce)').matches,FINE=matchMedia('(hover:hover) and (pointer:fine)').matches;
const resEl=$('#result'),btn=$('#go'),codeEl=$('#code'),regEl=$('#region');
const esc=s=>s==null?'':String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>{n=Number(n)||0;return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(n)};
const ic=n=>`<svg class="ic"><use href="#i-${n}"/></svg>`;

/* ---- reveal on scroll + count-up ---- */
function countUp(el){const to=+el.dataset.to||0,suf=el.dataset.suf||'';if(RM){el.textContent=fmt(to)+suf;return}
  const t0=performance.now(),D=1300;(function s(t){const p=Math.min((t-t0)/D,1);el.textContent=fmt(Math.round(to*(1-Math.pow(1-p,4))))+suf;if(p<1)requestAnimationFrame(s)})(t0)}
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');e.target.querySelectorAll('[data-to]').forEach(countUp);io.unobserve(e.target)}}),{threshold:.12,rootMargin:'0px 0px -40px 0px'});
const watch=r=>(r||document).querySelectorAll('.rv:not(.in)').forEach(el=>io.observe(el));
watch();

/* ---- tilt (pointer, rAF-throttled, transform only) ---- */
function bindTilt(root){if(!FINE||RM)return;(root||document).querySelectorAll('.tilt:not([data-t])').forEach(el=>{el.dataset.t=1;let f=0,ev;
  el.addEventListener('pointermove',e=>{ev=e;if(f)return;f=requestAnimationFrame(()=>{f=0;const r=el.getBoundingClientRect();
    el.style.setProperty('--ry',((ev.clientX-r.left)/r.width-.5)*14+'deg');el.style.setProperty('--rx',(.5-(ev.clientY-r.top)/r.height)*14+'deg')})});
  el.addEventListener('pointerleave',()=>{el.style.setProperty('--rx','0deg');el.style.setProperty('--ry','0deg')})})}
bindTilt();

/* ---- cursor glow + scroll progress ---- */
const glow=$('#glow'),bar=$('#bar');let gx=0,gy=0,gq=0,sq=0;
if(FINE&&!RM)addEventListener('pointermove',e=>{gx=e.clientX;gy=e.clientY;glow.style.opacity=1;if(!gq)gq=requestAnimationFrame(()=>{gq=0;glow.style.transform=`translate3d(${gx-250}px,${gy-250}px,0)`})},{passive:true});
addEventListener('scroll',()=>{if(sq)return;sq=requestAnimationFrame(()=>{sq=0;const h=document.documentElement.scrollHeight-innerHeight;bar.style.transform=`scaleX(${h>0?scrollY/h:0})`})},{passive:true});

/* ---- particle network (capped, paused when hidden) ---- */
const cv=$('#fx'),cx=cv.getContext('2d');let W,H,P=[],raf=0;
function size(){const d=Math.min(devicePixelRatio||1,1.5);W=innerWidth;H=innerHeight;cv.width=W*d;cv.height=H*d;cx.setTransform(d,0,0,d,0,0);
  P=Array.from({length:Math.min(48,Math.round(W*H/24000))},()=>({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.28,vy:(Math.random()-.5)*.28,r:Math.random()*1.5+.6}));if(RM)draw()}
function draw(){cx.clearRect(0,0,W,H);cx.fillStyle='rgba(196,181,253,.8)';
  for(const p of P){if(!RM){p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>W)p.vx*=-1;if(p.y<0||p.y>H)p.vy*=-1}cx.beginPath();cx.arc(p.x,p.y,p.r,0,6.283);cx.fill()}
  cx.lineWidth=1;for(let i=0;i<P.length;i++)for(let j=i+1;j<P.length;j++){const dx=P[i].x-P[j].x,dy=P[i].y-P[j].y,d=dx*dx+dy*dy;
    if(d<14400){cx.strokeStyle=`rgba(124,58,237,${.38*(1-d/14400)})`;cx.beginPath();cx.moveTo(P[i].x,P[i].y);cx.lineTo(P[j].x,P[j].y);cx.stroke()}}
  if(!RM)raf=requestAnimationFrame(draw)}
let rz;addEventListener('resize',()=>{clearTimeout(rz);rz=setTimeout(size,150)});
document.addEventListener('visibilitychange',()=>{if(RM)return;cancelAnimationFrame(raf);if(!document.hidden)raf=requestAnimationFrame(draw)});
size();if(!RM)raf=requestAnimationFrame(draw);

/* ---- toast + copy ---- */
let tt;function toast(m){const t=$('#toast');t.querySelector('span').textContent=m;t.classList.add('on');clearTimeout(tt);tt=setTimeout(()=>t.classList.remove('on'),1800)}
async function copy(txt){try{await navigator.clipboard.writeText(txt);toast('Copied to clipboard')}catch(e){toast('Copy failed')}}
document.addEventListener('click',e=>{const c=e.target.closest('.cp');if(!c)return;copy(c.dataset.u?location.origin+c.dataset.u:c.dataset.c)});
addEventListener('keydown',e=>{if(e.key==='/'&&document.activeElement!==codeEl&&!/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)){e.preventDefault();codeEl.focus()}});
codeEl.addEventListener('keydown',e=>{if(e.key==='Enter')fetchMap()});btn.addEventListener('click',fetchMap);

/* ---- fetch + render ---- */
function show(html){resEl.classList.remove('hidden');resEl.innerHTML=html;requestAnimationFrame(()=>requestAnimationFrame(()=>{watch(resEl);bindTilt(resEl);resEl.querySelectorAll('.rv').forEach(el=>{el.classList.add('in');el.querySelectorAll('[data-to]').forEach(countUp)})}))}
const showErr=m=>show(`<div class="alert">${ic('alert')}<span>${esc(m)}</span></div>`);
const skel=`<div class="card"><div class="mh"><div class="sk" style="width:132px;height:132px;border-radius:20px"></div><div style="flex:1"><div class="sk" style="height:30px;width:60%;margin-bottom:12px"></div><div class="sk" style="height:16px;width:35%;margin-bottom:12px"></div><div class="sk" style="height:16px;width:85%"></div></div></div><div class="chips"><div class="sk" style="height:44px;width:120px"></div><div class="sk" style="height:44px;width:120px"></div><div class="sk" style="height:44px;width:120px"></div></div></div>`;

async function fetchMap(){
  const code=codeEl.value.trim();if(!code){showErr('Please enter a map code first.');codeEl.focus();return}
  btn.disabled=true;btn.classList.add('ld');resEl.classList.remove('hidden');resEl.innerHTML=skel;
  resEl.scrollIntoView({behavior:RM?'auto':'smooth',block:'nearest'});
  try{
    const res=await fetch(`/public_info?map_code=${encodeURIComponent(code)}&region=${regEl.value}`);
    const data=await res.json();
    if(!res.ok||data.code!==0){showErr(data.msg||'Failed to fetch map info.');return}
    render(data.data);
  }catch(e){showErr('Network error: '+e.message)}
  finally{btn.disabled=false;btn.classList.remove('ld')}
}

function render(d){
  const m=d.map_info,g=d.game_info,im=d.images,t=d.timestamps,th=(im.backgrounds&&im.backgrounds[0])||im.share_image||'';
  const tags=(m.tags||[]).map((x,i)=>`<span class="tag rv" style="--i:${i+4}">${esc(x)}</span>`).join('');
  const dl=[[g.android_download,'Android'],[g.ios_download,'iOS'],[g.ugc_url,'UGC Portal']].filter(x=>x[0]).map(x=>`<a class="ghost" href="${esc(x[0])}" target="_blank" rel="noopener">${ic('dl')}${x[1]}</a>`).join('');
  show(`<div class="card">
   <div class="mh rv">
     ${th?`<img class="thumb tilt" src="${esc(th)}" alt="${esc(m.map_name)} preview" onerror="this.style.display='none'">`:''}
     <div><h3>${esc(m.map_name)}</h3><div class="by">by <b>${esc(m.author_name)}</b></div>${m.description?`<div class="desc">${esc(m.description)}</div>`:''}</div>
   </div>
   <div class="chips rv" style="--i:2">
     <div class="chip">${ic('heart')}<span data-to="${+m.like_count||0}">0</span> Likes</div>
     <div class="chip">${ic('bell')}<span data-to="${+m.subscribe_count||0}">0</span> Subs</div>
     <div class="chip">${ic('users')}${esc(m.team_count)} Teams</div>
     <div class="chip">${ic('clock')}${esc(m.estimated_play_time)}</div>
   </div>
   ${tags?`<div class="tags">${tags}</div>`:''}
   <div class="st rv">Map Info</div>
   <div class="grid">
     <div class="tile rv" style="--i:0"><div class="l">Workshop Code</div><div class="v m" style="padding-right:40px">${esc(m.workshop_code)}</div><button class="cp" type="button" aria-label="Copy workshop code" data-c="${esc(m.workshop_code)}">${ic('copy')}</button></div>
     <div class="tile rv" style="--i:1"><div class="l">Game Mode</div><div class="v">${esc(m.game_mode)}</div></div>
     <div class="tile rv" style="--i:2"><div class="l">Region</div><div class="v">${esc(g.region)}</div></div>
     <div class="tile rv" style="--i:3"><div class="l">Language</div><div class="v">${esc(g.language)}</div></div>
   </div>
   <div class="st rv">Timestamps</div>
   <div class="grid">
     <div class="tile rv"><div class="l">Start</div><div class="v m">${esc(t.start_time_formatted)}</div></div>
     <div class="tile rv" style="--i:1"><div class="l">End</div><div class="v m">${esc(t.end_time_formatted)}</div></div>
   </div>
   ${dl?`<div class="st rv">Downloads</div><div class="acts rv">${dl}</div>`:''}
   <div class="rv" style="margin-top:24px"><button class="ghost" type="button" id="rawBtn" aria-expanded="false">${ic('code')}View Raw JSON</button><pre class="raw hidden" id="rawBox">${esc(JSON.stringify(d,null,2))}</pre></div>
  </div>`);
}
document.addEventListener('click',e=>{const b=e.target.closest('#rawBtn');if(!b)return;const box=$('#rawBox'),open=box.classList.toggle('hidden');
  b.setAttribute('aria-expanded',!open);b.lastChild.textContent=open?'View Raw JSON':'Hide Raw JSON'});
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════
# 🚀 RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print("╔═══════════════════════════════════════════╗")
    print("║      💎 CRYSTAL APIs — Starting...        ║")
    print("╠═══════════════════════════════════════════╣")
    print(f"║  🌐 Website   : http://0.0.0.0:{port}/")
    print(f"║  🌉 Public API: /public_info  (no pwd)    ║")
    print(f"║  🔒 Private   : /info & /map_details      ║")
    print(f"║  👨‍💻 Dev       : @srking5306               ║")
    print(f"║  💎 Credits   : @f1api                   ║")
    print("╚═══════════════════════════════════════════╝")
    app.run(host='0.0.0.0', port=port, debug=False)