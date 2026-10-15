# OM Suite - guide (kya kahan hai, kaise chalana hai, purane projects se kaise shift karna hai)

## 1. Purane projects → naya app

| Purana project / tool | Naya app me | URL |
|---|---|---|
| working-paid-topay (Netlify, `index.html`) | **Paid / ToPay Suite** (7 modes + CN Query + OmInsights fetch) | `/paid-topay/` |
| DBP-REPORT (Streamlit) → Report Builder | **DBP Report Builder** | `/dbp/report` |
| … Month-wise Summary (ADR / NET / Full Region) | **Month-wise Summary** | `/dbp/month` |
| … AI Chatbot | **AI Chatbot** | `/dbp/chatbot` |
| … Validation | **Validation & Audit** | `/dbp/validation` |
| … Mail Center + Thunderbird bridge | **Mail Center** + **Thunderbird setup** | `/dbp/mail/`, `/dbp/mail/bridge` |
| … Directory update | **Directory & Masters** (sirf admin) | `/dbp/directory` |
| om-suite → TBB Party Mail | **TBB Party Mail** | `/mail/tbb/` |
| … Bill Mail | **Bill Generated Mail** | `/mail/bill/` |
| … Previous Mail | **Previous Mail & Format** | `/mail/previous/` |
| … Branch Finder / Customer Search | **Branch Detail Finder**, **Customer Search** | `/branch/`, `/customer/` |
| … OM Automation (CN / GST / Party / Master) | **OM Automation** | `/om/` |
| om-suite → gr_auto_modification_system | alag app (apna login) - sirf dashboard tile | `GR_AUTO_URL` |
| Users / login (4 alag systems) | **ek login**, admin approval, per-user tool access | `/admin/users` |

Ek jagah: `core/modules.py` me har tool ki entry hai (sidebar, dashboard card aur admin access wahi se aate hain).

## 2. Pehli baar deploy (Render + Firebase)
1. Code ko **private** GitHub repo me daalein (`dbp/masters_seed/` me employee directory hai).
2. Firebase console → project `ptp-report` → Firestore Database banayein → Project settings → Service accounts → *Generate new private key*.
3. Render → New → **Blueprint** → repo (ye `render.yaml` + `Dockerfile` use karta hai).
4. Env vars: `FIREBASE_SERVICE_ACCOUNT_JSON` (poori JSON), `ADMIN_EMAILS`, `PUBLIC_URL` (`https://<app>.onrender.com`), `GR_AUTO_URL` (optional). `SECRET_KEY` Render khud banata hai.
5. Deploy ke baad `/healthz` kholein: `"store": "firestore"` dikhna chahiye (`"local"` = Firebase set nahi hua, users redeploy par chale jayenge).
6. `ADMIN_EMAILS` wale email se signup karein - wo turant admin ban jata hai. Baaki log signup karenge → admin *Users & Access* me approve karke tools tick karega.
7. Firestore rules me client access band karein (README me rule diya hai) - **purana Netlify Paid/ToPay band hone ke baad**.

## 3. Migration checklist
- [ ] Sab users naye app par **signup** karein (purane password hash format alag hain, import nahi hote); admin approve + tools tick.
- [ ] Har user: *Settings* me apna **OmInsights login** (Paid/ToPay fetch) aur zarurat ho to **SMTP** save kare.
- [ ] Har mail user: Mail Center → *Thunderbird setup* se extension zip + connection code. **Purane dono extensions remove** karein ("Auto Bill Intelligence Bridge", "OM Report AI Mail Bridge").
- [ ] Admin: *Directory & Masters* me latest directory / branch master check karein (permanent karne ke liye `dbp/masters_seed/` me commit).
- [ ] Region ki branch lists (Month-wise → "branch list") naye app me dobara save karein.
- [ ] `GR_AUTO_URL` set karein (gr_auto ka apna deploy chalta rahega).
- [ ] Purane deploys band karein: Netlify (Paid/ToPay), Streamlit Cloud (DBP), purana Render om-suite `main_app`; local `ominsights_proxy.py` ab nahi chahiye.
- [ ] `gr_auto_modification_system/.env` me real tokens the to unhe **rotate** karein; Firebase service-account JSON kabhi GitHub par nahi.

## 4. Network par depend karne wali cheezein (deploy ke baad ek-ek karke test karein)
| Feature | Kahan se data / bhejna | Agar Render se na khule to |
|---|---|---|
| OmInsights fetch | `ominsights.omlogistics.co.in` | Settings me "verify nahi hua" warning; manual Excel upload use karein |
| Branch / Customer / Party lookup | `scmomsanchar.omlogistics.co.in` | error me portal ka page title aata hai (login/access) |
| CN Query | `load.omone.in` | - |
| SMTP mail | aapka SMTP host | Thunderbird se bhejein (server ko mail server nahi chahiye) |
| GST Query | `services.gst.gov.in` + Chrome | Docker image + paid plan (RAM) chahiye |
Agar internal portals sirf office network par khulte hain to app ko office ke andar host karna padega (ya VPN wala server).

## 5. Env vars (poori list `.env.example` me)
`SECRET_KEY`, `ADMIN_EMAILS`, `FIREBASE_*`, `PUBLIC_URL`, `GR_AUTO_URL`, `OMINSIGHTS_*`, `BRANCH_URL`, `CUSTOMER_URL` / `CUSTOMER_PORTAL_USER_ID`, `CN_API_URL`, `SMTP_DEFAULT_*`, `GEMINI_ADMIN_API_KEY` (optional), `GST_MAX_SESSIONS`, `CREDENTIAL_KEY` / `BRIDGE_MASTER` (optional; default `SECRET_KEY` se).
**`SECRET_KEY` badalne par:** sab login nikal jayenge, saved OmInsights/SMTP passwords dobara daalne honge, saare Thunderbird tokens badal jayenge (naya connection code).

## 6. Rozmarra admin kaam
- Naye signup approve karna aur tools dena (naya module aane par purane users ko tick karna padta hai).
- User block → turant logout. Password reset → temporary password ek hi baar dikhta hai.
- *Audit log* me login, signup, admin actions, mail sends, OmInsights fetch dikhte hain.

## 7. Aksar aane wale sawal
- **"Unexpected token '<'" / "Server ne data ki jagah page bheja"** → page ko Ctrl+F5 se refresh karein (purana cached tab); phir bhi aaye to login expire hua hoga.
- **Redeploy ke baad users/saved settings gayab** → `/healthz` me `store: local` hai; Firebase JSON set karein.
- **Redeploy ke baad chalte jobs / GST session / mail queue gayab** → ye memory/disk me hain; dobara chalayein (jobs ~24h tak rehte hain).
- **GST "browser abhi doosre user ke use me hai"** → ek waqt me `GST_MAX_SESSIONS` session; kuch minute baad.
- **Mail "queued" par atki** → Thunderbird khula hai? Mail Center → *Thunderbird setup* par "Extension connected" dikhna chahiye; extension popup me Sync.
- **403** → admin ne wo tool tick nahi kiya.
- **Free Render plan** pehli request par sleep se jagne me 30-60 sec leta hai; Chrome/bade Excel ke liye `starter` plan.

## 8. Jo port nahi hua / badla
Streamlit ka login video background, "server folder se auto-pick", month-wise "AI double-check" button; per-user AI keys; SMTP ki IMAP "Sent copy"; om-suite ka alag per-user mail/API-credential system (ab Settings me OmInsights + SMTP). Naye: test mode (mail tools), "Reviewed" gate, private per-user jobs, ek merged extension.

## 9. Code ka naksha
```
app.py, wsgi.py        app factory / gunicorn      core/        config, store(Firestore|SQLite), auth, modules, jobs, bridge_queue, insights
blueprints/            har tool ka web layer       dbp/         DBP pandas engine + masters + mail planning
mailflows/             TBB / Bill / Previous engine omauto/      OM Automation (CN/GST/Party/Master) engine
templates/, static/    shell + tools UI            thunderbird_extension/   merged extension v2.0.0
tests/                 201 tests                   Dockerfile, render.yaml
```
Naya tool jodne ke liye: `core/modules.py` me entry + ek blueprint + template.
