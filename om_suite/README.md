# OM Suite - unified web app

Teen alag projects (Paid/ToPay, OM Suite, DBP Report) ko ek app me jodne ka project.
**Shuru yahan se: [`docs/GUIDE.md`](docs/GUIDE.md)** (purane projects → naya app, deploy aur migration checklist, FAQ).
Ek login, ek design (light, white-blue), mobile par installable (PWA), admin approval + per-user tool access.

## Build status

| Phase | Kya hai | Status |
|---|---|---|
| **1** | Shell, login/signup + admin approval, Users & Access, Settings, Paid/ToPay Suite (saare 7 modes + CN Query + OmInsights fetch), Docker/Render, PWA | **Ready (is zip me)** |
| **2a** | DBP Report Builder, Validation & Audit, Month-wise Summary (ADR / NET / Full Region), AI Chatbot, Directory & Masters | **Ready** |
| **2b** | Mail Center (branch drafts, region auto-mail, full-report mail) via Thunderbird bridge or SMTP + one merged Thunderbird extension | **Ready** |
| **3a** | Branch Detail Finder, Customer Search, OM Automation (CN / GST captcha / Party / Master) | **Ready** |
| **3b** | TBB Party Mail, Bill Generated Mail, Previous Mail & Format | **Ready** |
| - | GR Auto Modification: alag app, sirf tile/link (apna login) | Tile ready, `GR_AUTO_URL` set karna hai |

## Local chalana

```bash
pip install -r requirements.txt
cp .env.example .env        # optional
python app.py               # http://localhost:5000
pip install -r requirements-dev.txt
python -m pytest tests      # 153 tests (89 are the original DBP engine tests, unchanged logic)
```
`ADMIN_EMAILS` wala email signup karte hi admin ban jata hai. Agar `ADMIN_EMAILS` khali ho to pehla signup admin banta hai.
Bina Firebase ke app local SQLite (`data/omsuite.db`) use karta hai - sirf development ke liye.

## Firebase (persistent users) - ek baar ka setup

1. Firebase console -> project **ptp-report** -> *Build -> Firestore Database* (agar nahi bana to Create database).
2. *Project settings -> Service accounts -> Generate new private key* -> JSON file download hogi.
3. Us JSON ka poora content Render me env var **`FIREBASE_SERVICE_ACCOUNT_JSON`** me paste karo (ya local me `FIREBASE_SERVICE_ACCOUNT_FILE=path`). **Is JSON ko GitHub par kabhi mat daalna.**
4. Firestore *Rules* me sab client access band kar do (server Admin SDK rules bypass karta hai):
   ```
   rules_version = '2';
   service cloud.firestore { match /databases/{database}/documents { match /{document=**} { allow read, write: if false; } } }
   ```
   Dhyan: purana Paid/ToPay (Netlify) abhi Firestore me seedha API key se `ptp_auth/store` padhta hai - ye rule lagate hi wo purana login band ho jayega. Naye app par shift hone ke baad hi lagao.

Users collection `omsuite_users`, audit `omsuite_audit` me jate hain (naya data; purane `ptp_auth/store` ko ye app nahi chhoota).

## Render par deploy (Docker)

1. Is folder ko GitHub repo me push karo.
2. Render -> New -> **Blueprint** -> repo choose karo (`render.yaml` apne aap padh leta hai).
3. Env vars: `FIREBASE_SERVICE_ACCOUNT_JSON` (upar wala), `ADMIN_EMAILS` (check karo), `GR_AUTO_URL` (GR Auto ka URL, optional). `SECRET_KEY` Render khud banata hai.
4. Deploy ke baad `https://<app>.onrender.com/healthz` me `"store": "firestore"` dikhna chahiye. Agar `"local"` dikhe to Firebase JSON set nahi hua - users redeploy par ud jayenge.

Lambe OmInsights exports ke liye gunicorn timeout 900 s hai (`Dockerfile`). Ek saath max 3 exports chalte hain, baaki ko "busy" milta hai.

Docker image me Chromium bhi hai (GST Query ke liye, Phase 3). Free plan ki RAM kam hai - GST Query chalane ke time `plan: starter` karna padega. Abhi ke liye Chrome nahi chahiye: `--build-arg INSTALL_CHROME=0`.

## DBP tools (Phase 2a)

* Pandas engine `dbp/` Streamlit app se aaya hai - **calculation logic same hai** (original 85 tests ab bhi pass hote hain, `tests/engine/`). Sirf screens naye shell me dobara bani hain.
* Har user ka apna workspace hota hai (`data/workspaces/<user>/`): upload hui files, generated report, chat history. 7 din purane workspace apne aap delete ho jate hain.
* **Directory & Masters** (sirf admin): directory aur branch master yahin se update hote hain; galat file upload hui to purani file safe rehti hai. Bundled copy `dbp/masters_seed/` me hai (first start par `data/masters/` me copy hoti hai). **Is folder me employee directory data hai - repo ko private rakhein.**
* Render ki disk free plan par har redeploy par reset hoti hai: admin ne jo directory upload ki wo redeploy par seed wali par wapas aa jayegi. Permanent karne ke liye nayi file ko `dbp/masters_seed/` me commit karein (ya Render persistent disk lagayein).
* `GEMINI_ADMIN_API_KEY` optional hai (chatbot ki wording ke liye). Bina key ke chatbot data se seedha jawab deta hai.
* Badi Excel files pandas me RAM leti hain; Render free (512 MB) par bahut badi files par memory kam pad sakti hai.
* Streamlit se jo cheezein nahi layi gayi: "server folder se auto-pick" (web server par meaningless), login page ka video background, month-wise "AI double-check" button.

## Mail Center + Thunderbird (Phase 2b)

* **Mail Center** (sidebar → Communication): *Single Branch Draft* (.eml + data sheet download), *Auto Mail — All Region* (plan → preview → send) aur *Send Full DBP Report*. Test mode default ON; real recipients ko tabhi jati hai jab test mode band karo aur confirm karo.
* **Bhejne ke 2 tareeke:**
  1. **Thunderbird (recommended):** mails queue hoti hain aur har user ke apne Thunderbird se jati hain. Server ko mail server tak pahunchne ki zarurat nahi.
  2. **SMTP:** har user apna SMTP login *Settings → Mail sending* me save karta hai (verify hota hai, password encrypted). Ek baar me max 150 mails; server ko SMTP host reach karna chahiye (`mail.omlogistics.co.in` Render se khulta hai ya nahi, ye pehle Settings me test karke dekhein).
* **Extension:** `thunderbird_extension/` (v2.0.0) purane dono extensions ("Auto Bill Intelligence Bridge", "OM Report AI Mail Bridge") ki jagah leta hai. Mail Center → *Thunderbird setup* page se zip download + **connection code** milta hai; code extension Options me paste karte hi URL/user/token set ho jata hai. Status page par "Extension connected" dikhta hai.
* Har user ka token alag hai (HMAC), "Naya code banao" se purana turant band. Jobs aur attachments sirf usi user ko milte hain. Queue SQLite me (`data/bridge/queue.db`) hai - ek hi Render instance ke liye theek hai; redeploy par pending jobs chali jati hain.
* Permanent install: Thunderbird unsigned extension ko restart par hata deta hai. Signed `.xpi` ke liye extension Mozilla/Thunderbird add-on portal par self-distribution ke liye submit karni hoti hai (steps portal ke developer docs me).

## Lookup tools (Phase 3a)

* **Branch Finder / Customer Search / OM Automation** ka scraping logic original apps jaisa hai; sirf UI naye shell me aur background jobs ab per-user private hain (`core/jobs.py`): doosra user aapka job status/download dekh nahi sakta.
* **Internal portals reach hone chahiye:** ye tools `scmomsanchar.omlogistics.co.in` (branch/customer pages) aur `load.omone.in` (CN API) se data laate hain - server ko ye addresses khulte hone chahiye (OmInsights jaisa hi sawaal). Customer page ka URL me ek portal user id (`CUSTOMER_PORTAL_USER_ID`, default 39942) hai.
* **GST Query** real headless Chrome chalata hai aur captcha **aapko khud type karna hota hai** (auto-solve nahi). Isme Chrome wala Docker image chahiye; har session ~300 MB RAM leta hai, isliye ek waqt me `GST_MAX_SESSIONS` (default 1) hi chalte hain, idle session 10 minute baad band ho jata hai. Free plan (512 MB) par ye tight hai - `starter` plan lein.
* **Ek gunicorn worker:** GST browser session, background jobs aur login limiter process ki memory me rehte hain, isliye `Dockerfile` me `--workers 1 --threads 12` hai. Zyada workers karne par GST/jobs "session expired / unknown job" dikhayenge.
* Jobs 24 ghante baad apne aap delete ho jate hain; redeploy par chal rahe jobs rukk jate hain.
* **Static files ab content-hash se aate hain** (`?v=<hash>`): koi bhi JS/CSS badalne par URL badal jata hai, isliye browser/service-worker ki purani cached script kabhi use nahi hoti. Update ke baad ek baar page refresh (Ctrl+F5) kar lein.
* **API ke errors hamesha JSON** (`/om/api/…`, `/jobs/…`, `/customer/api/…`, chatbot, mail progress): login expire / CSRF / server error par bhi page ko JSON milta hai aur script saaf message dikhati hai, "Unexpected token <" nahi.
* Party Code / Customer lookup ab portal ko wahi request bhejta hai jo browser bhejta hai (saari form fields + portal ke Accept/Origin/Referer headers). Pehle CN API ke headers (`Origin: ops.omone.in`) galti se portal ko bhi chale jate the.
* "AI SUMMARY" jo CN card par tha wo asal me rule-based text hai (AI nahi) - label ab "SUMMARY" hai.

## TBB / Bill / Previous Mail (Phase 3b)

* Teeno tools ek hi engine par chalte hain (`mailflows/`, `blueprints/flows.py`): **upload → preview/review → Confirm → send**. Original rules same hain: naya/badla content = fresh DRAFT, **same content dobara upload karne par SENT mail kabhi dobara nahi jati**, Confirm par tick wale DRAFT → QUEUED aur baaki → SKIPPED (kabhi nahi bheji jaati), FAILED rows retry ho sakti hain.
* **Recipients** company directory se aate hain - wahi file jo admin *Directory & Masters* me rakhta hai (alag directory upload ki zarurat nahi).
* **Test mode (naya, default ON):** mails sirf aapke test address par jati hain, rows ka status nahi badalta, isliye baad me asli send ho sakta hai.
* **"Reviewed" gate:** "Send all" sirf QUEUED + reviewed rows bhejta hai. Original me ye gate AI check se khulta tha; ab AI na ho to aap row check karke **✔ Reviewed** (ya "Mark all with email as Reviewed") dabate ho. AI check/AI Fix (typo-domain sudhaar + directory se email) optional hai; AI ke liye server par `GEMINI_ADMIN_API_KEY` set karein (per-user AI keys port nahi ki gayi).
* **Thunderbird se bhejna:** row pehle SENDING hoti hai, extension ka result aane par apne aap SENT/FAILED ho jati hai (page har 5 sec me refresh karta hai). **SMTP** me turant SENT/FAILED; IMAP "Sent folder copy" wali feature port nahi ki (Thunderbird se bhejne par Sent folder me copy apne aap aati hai). SMTP/Thunderbird dono ke liye `Settings` aur *Thunderbird setup* wahi hain jo Mail Center ke hain.
* **Previous Mail & Format:** purani mail (.eml ya manual) + naya data sheet → template (`{{Column}}` placeholders; AI ho to wo banata hai, warna aap) → group by column → har group ek mail. 🔎 button Thunderbird ke Sent folder me purani mail dhoond kar To/CC/BCC suggest karta hai (extension ka "match" feature).
* **Bill / Previous** ki attachment CSV hai, **TBB** ki xlsx. (Original me Bill ki SMTP mail bina attachment thi; ab dono tareeko me CSV jaati hai.)
* **History page:** `Mail/…/history` me aapke Thunderbird bridge ki recent jobs (sirf aapki).
* Naya module `previous_mail` hai: purane users ko admin *Users & Access* me tick karna hoga.
* TBB-only / Bill-only users bhi Thunderbird setup page use kar sakte hain (bridge sab mail tools ke liye ek hi hai).

## ADR / NET Billing Suite (`/billing/`)
Teen HTML tools (ADR NET Query, Branch-wise Data, Region Creation) ka audited, merged version - bugs aur fixes `docs/BILLING_SUITE_AUDIT.md` me. Poora kaam browser me (`static/modules/billing/`); server par kuch process/save nahi hota.
Dev tests: `npm install` (sirf pehli baar, repo root me) phir `pytest` - Node tests IST aur New York time zone me chalte hain; node na ho to wo tests skip ho jate hain.

## Jaan lena zaroori

* **OmInsights fetch (Paid/ToPay):** ab local `ominsights_proxy.py` ki zarurat nahi. Har user **Settings -> OmInsights login** me apna username/password ek baar save karta hai (login verify hota hai, password encrypted store hota hai). Server uske naam se OmInsights se data laata hai. Sirf card 546 aur 967 allowed hain (`OMINSIGHTS_ALLOWED_CARDS`) - ye open proxy nahi hai. **Zaroori:** OmInsights ko Render ka server khud reach kar pana chahiye. Agar wo sirf office network/VPN par khulta hai to ye buttons Render par kaam nahi karenge (Settings me "verify nahi hua" warning aayegi).
* Paid/ToPay ka calculation code bilkul wahi hai jo pehle tha (browser me chalta hai) - sirf design aur navigation naya hai, login hata kar shell ka login lagaya hai.
* Purane Paid/ToPay users naye app me automatic nahi aate (password hash format alag hai). Sab ko ek baar signup karna hoga, admin approve karega.
* Auto Mail abhi `mailto:`/.eml draft banata hai; "Send via Thunderbird" bridge Phase 2 me Mail Center ke saath aayega.

## Structure

```
app.py, wsgi.py            app factory / gunicorn entry
core/                      config, store (Firestore|SQLite), auth, security, module registry
blueprints/                main (dashboard/settings), admin, paid_topay
templates/, static/        shell + design tokens; static/modules/paid_topay = the tool
tests/                     pytest
Dockerfile, render.yaml
```
Naya tool jodne ke liye: `core/modules.py` me entry, ek blueprint, ek template - baaki (sidebar, dashboard card, admin access) apne aap ho jata hai.
