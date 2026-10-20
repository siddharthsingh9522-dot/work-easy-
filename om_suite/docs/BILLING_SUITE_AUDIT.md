# ADR / NET Billing Suite - audit report

Teen standalone HTML tools (`ADR_NET_QUERY.html`, `BRANCH_WISE_DATA_CREATION.html`, `REGION_CREATION.html`) ko line-by-line padha, chalakar test kiya
aur ek hi option me merge kiya: **Reports → ADR / NET Billing Suite** (`/billing/`). Files ab bhi sirf browser me process hoti hain (kuch upload nahi hota).
Original ADR tool ka credit: *ADR NET QUERY - Created by Harsh*.

## Kya verify kiya (sirf padhkar nahi)
* Har bug ko ya to **original HTML ko jsdom me asli SheetJS ke saath chalakar** reproduce kiya, ya Node me chhota script chalakar.
* **Differential tests:** wahi data original tool aur naye engine dono me - ADR tool (400 random rows) ke saare counts **identical**; Branch-wise ke 10 sheets ke naam + row counts **identical**; Region ke ADR / NET POD / PARTY WISE / SUMMARY sheets **cell-for-cell identical** (SUMMARY ke liye original me sirf ek line patch ki, kyunki wo kabhi chalta hi nahi tha - neeche C1).
* Naye engine ke **34 JS tests** (27 engine + 7 UI) 4 time zones me pass (IST, UTC, New York, Auckland); repo me `pytest` unhe IST aur New York me chalata hai.

## Bugs mile aur fix hue
| # | Tool | Severity | Kya galat tha (kaise verify hua) | Fix |
|---|---|---|---|---|
| A1 | ADR | **High** | `renderTable` cell values ko `innerHTML` me daalta tha - file ke cell me `<img onerror=...>` ho to wo live element ban gaya (verified). Is app me ye logged-in session me chalta. | Saara DOM `textContent` se; test: cell me HTML ka text dikhta hai, element nahi banta. |
| A2 | ADR | **High** | Header me thoda sa farq (jaise `CN_POD_UPLOAD_STS ` trailing space) to **saari valid rows "POD status empty" bata kar delete** (verified: Remaining 3 → 0, Deleted 4). Koi warning nahi. | Header matching case/space/underscore se azaad; column-mapping panel; required column set nahi to **run block** (ya "skip stage" jaan-boojh kar), Summary sheet me likha jata hai. |
| A3 | ADR | Med | Export me sirf 2 sheet; comments me jo "Raw Data / Removed Records / Summary" ka zikr tha wo exist hi nahi karta - delete hui rows ka koi record nahi. | Optional sheets: **Removed Records (stage + reason)**, **Summary** (counts, filters, columns, warnings, reconciliation), **Raw Data**. |
| A4 | ADR | Med | Date fallback `new Date(string)`: `"2026"` → 1-Jan-2026, serial text `"45000"` → saal 45000, `31-FEB` aage khisak jata, ISO date UTC me parse. | Strict parser (sirf known formats, real calendar), na-padhne wali dates **kept + count warning**. |
| A5 | ADR | Med | Asli Excel date cells export me sirf serial number (46143) ban jate the. | `DD-MMM-YYYY` text. |
| A6 | ADR | Low | Sirf pehli sheet; CSV me leading zeros; duplicate/blank headers; blank rows; duplicate CN ka pata nahi. | Sheet picker (required columns wali sheet auto), CSV raw text, unique headers, blank rows skip, duplicate-CN warning. |
| A7 | ADR | Low | Freight modes hardcoded (`TO PAY`, `PREPAID`); koi dekh nahi sakta ki file me `PAID` jaisa mode bhi hai. | Saare modes counts ke saath chips me; tick/untick + naya mode add. |
| B1 | Branch-wise | **High** | **Single-branch mode bina Summary ke crash**: `Cannot read properties of null (reading 'partyWarnings')` (verified). | Fix + ab ek ya kai branch codes. |
| B2 | Branch-wise | Med | Summary me title/banner row party ban sakti thi aur uske neeche ki saari branch rows nigal leti (party sheet me chali jati). | Banner guard (`REPORT / PENDING BILLING / NOT BILLED / REGION…`, lambi/multi-line text party nahi). |
| B3 | Branch-wise | Med | Har branch ke liye poori raw sheet dobara scan (rows × branches) - bade data par browser freeze. | Ek pass index; 60,000 rows × 300 branches **~0.7 sec** (read+prepare 0.2 sec). |
| B4 | Branch-wise | Low | Generic `CODE` header, `B.CODE` se pehle ho to wahi chun leta. | Priority: `B.CODE` > `BRANCH CODE` > … > `CODE`. |
| B5 | Branch-wise | Low | Party column sirf literal `PARTY/CUSTOMER…` header par milta; `BILLING PARTY NAME` par party sheets chupchap skip. Branch-name na milne par rows chupchap drop. 50+ warnings ka dher. | Billing-party pehle; name-mismatch ka count warning; warnings group me; **Summary me na likhi raw branches** ka audit. |
| C1 | Region | **High** | **SUMMARY sheet kabhi banti hi nahi**: upload handler `monthCols / totalLabel / blockTitles` ko save hi nahi karta, isliye hamesha "no month-wise columns" (verified - perfect Summary ke saath bhi). | Fix; aur agar Summary me month columns nahi to **data se months bana** (option). |
| C2 | Region | **High (IST)** | Asli Excel date cell, mahine ki **1 tareekh** = pichhle mahine me gini jati: SheetJS `cellDates` IST me `30-Apr 23:59:50` deta hai (verified `TZ=Asia/Kolkata`). | `cellDates` hata diya; dates seedhe calendar (y/m/d) se - time zone se azaad. |
| C3 | Region | Med | Source sheet ke **merged cells** naye (re-laid-out) sheets par copy ho jate the - alag cells merge ho kar data chhup sakta tha. | Merges copy nahi hote (column widths rehte hain). |
| C4 | Region | Med | `findBillingPartyColumn` kuch na milne par **column 0** laut deta tha (score 0 > -1) - "column mila" dikhta, matching galat. | Ab -1 → clear message / manual pick. |
| C5 | Region | Med | Blank / range se purani dates chupchap **last month** me; koi count nahi. | Counts + warning, aur option: older → *first* month. (Default original rule.) |
| C6 | Region | Low | Do party names ka prefix overlap ho to rows dono sections me, bina bataye. | Overlap warning; "Exact name" matching option. |
| C7 | Region | Low | Region ke bahar / B.CODE-blank rows ka koi hisaab nahi. | **Reconciliation**: raw = branch + party + region ke bahar; optional AUDIT sheet. SUMMARY me digit-only B.CODE ab number (lookups chalte), column widths. |
| X1 | Teeno | Med | ADR tool ka export `ADR Report` / `Remaining LR Report` naam deta hai, baaki do tools `ADR MADE BUT NOT BILLED` / `NET POD BUT NOT BILLED` maangte - seedha chain nahi hota. Teeno me parsing ka code 3 baar copy. | Dono naam samjhe jate hain; **"Send to Branch-wise / Region"** se in-memory hand-off (download/re-upload nahi); ek hi engine (`static/modules/billing/core.js`). |

## Jo maine badla NAHI (aapka faisla chahiye)
1. **Blank / purani date → last month** (SUMMARY): original rule rakha, par ab count dikhta hai. Aapke data me 700 me se 76 rows blank date wali thi (test data) - dekhna chahiye ki asal me ye sahi hai.
2. **B.CODE kahan se?** LR export me `B.CODE` nahi dikhta; hand-off ke baad aapko Billing-branch wala column ek baar chunna padta hai (UI poochta hai, yaad nahi rakhta).
3. **Freight modes** abhi bhi default `TO PAY` + `PREPAID` hain; file me `PAID` jaisa mode ho to chips me dikhega - zaroorat ho to tick karein.
4. SheetJS **0.18.5** (baaki pages jaisa) purana version hai; ye sirf aapki apni files browser me padhta hai, par agle update me naya version lagana behtar hoga.
5. Excel styles (rang, bold) community SheetJS se likhe nahi jate - original tools me bhi aisa hi tha.

## Files
`static/modules/billing/{core,app}.js`, `billing.css` · `blueprints/billing.py` · `templates/modules/billing.html` · tests: `tests/js/*.test.js`, `tests/test_billing_suite.py`.
