# -*- coding: utf-8 -*-
"""
config.py
Sabhi settings ek jagah — sheet-category detect karne ke rules, default
Gemini model, column-name synonyms. Naya region ya naya column naam aaye
to sirf yahi file edit karo, baaki code chhedne ki zaroorat nahi.
"""

# Sheet ka naam dekh kar hum usko ek "category" mein daalte hain.
# Key = internal category name, Value = list of keywords (sheet name mein
# case-insensitive dhundhe jaate hain). Jo bhi pehla match ho jaye, wahi
# category maan li jaati hai (order matters — upar wala pehle check hota hai).
SHEET_CATEGORY_RULES = [
    ("PAID", ["paid"]),
    ("TOPAY", ["topay", "to pay", "to-pay"]),
    ("PENDING_BILLING", ["adr made but not billed", "pending billing"]),
    ("POD_NOT_BILLED", ["net pod made but not billed", "pod on net", "not billed"]),
    ("DDR_PENDING", ["ddr"]),
    ("SUMMARY", ["summary"]),
    ("DATA", ["data"]),  # generic single-sheet reports jaise GURGAON DDR ki "DATA" sheet
]

# Yeh categories jo hum khud SUMMARY mein count karte hain (order bhi yahi
# rahega report mein). SUMMARY / DATA ko report banate waqt ignore karte hain
# (SUMMARY = purana output, DATA = usually DDR jaisa hi raw hota hai, alag
# se handle hota hai).
COUNTABLE_CATEGORIES = ["PAID", "TOPAY", "PENDING_BILLING", "POD_NOT_BILLED", "DDR_PENDING"]

# Har raw sheet mein branch pehchaanne ke liye column-name candidates,
# priority order mein (jo pehla mil jaye, wahi use hoga). Yeh sirf FALLBACK
# hai jab category CATEGORY_BRANCH_COLUMN mein confirm na ho.
BRANCH_CODE_COLUMNS = [
    "B.CODE", "B_CODE", "BILL_BRANCH_CODE", "CN_CURRENT_BRANCH_CODE",
]
BRANCH_NAME_COLUMNS = ["BRANCH", "DESTINATION_BRANCH", "SOURCE_BRANCH"]

# --- CONFIRMED BY USER (2026-09-27, cross-checked against the explicit
# 'BRANCH CODE' column already present in raw OmInsights exports — ADR BRANCH
# matched 1389/1411 = ~98% of ground truth for PENDING_BILLING, vs only 1343/1411
# for BILL_BRANCH_CODE; BILL_BRANCH_CODE matched 201/225 = ~89% for
# POD_NOT_BILLED, vs only 136/225 for CUSTOMER_BILL_LOC) — har category ke
# liye branch column YEH hi use karo, koi guess/fallback nahi: ---
CATEGORY_BRANCH_COLUMN = {
    "PAID": "B.CODE",
    "TOPAY": "B.CODE",
    "PENDING_BILLING": "ADR BRANCH",            # ADR/Pending Billing branch truth
    "POD_NOT_BILLED": "BILL_BRANCH_CODE",       # NET/POD-on-NET branch truth
    "DDR_PENDING": "DLY_CHALLAN_FROM",
}

# PENDING_BILLING ke liye asli branch column 'ADR BRANCH' hai. Kuch exports
# mein yeh column hi nahi hota -- unme branch 'ADR_NO_DATE_BRANCH' jaise ek
# combo-column ke ANDAR chhupa hota hai, format:
#   "<ADR NO>,<ADR DATE>,<ADR BRANCH>"   e.g. "19202517000399,29-JAN-26,530"
# CONFIRMED: comma se split karne par HAMESHA teesra/LAST token hi branch
# code hota hai. Neeche di gayi alias-lists ke through column header thoda
# bhi alag likha ho (space/underscore/hyphen/case), phir bhi match ho jaata
# hai -- ingest._find_column_by_aliases() in sabko normalize karke compare
# karta hai, isliye style/format badalne par bhi data break nahi hota.
ADR_BRANCH_COLUMN_ALIASES = [
    "ADR BRANCH", "ADR_BRANCH", "ADR-BRANCH", "ADRBRANCH",
    "ADR BRANCH CODE", "ADR_BRANCH_CODE", "ADR BR", "ADR BR CODE",
]
ADR_NO_DATE_BRANCH_COLUMN_ALIASES = [
    "ADR_NO_DATE_BRANCH", "ADR NO DATE BRANCH", "ADR_NO_DATE_BRANCH_CODE",
    "ADR NO,DATE,BRANCH", "ADRNODATEBRANCH", "ADR NO DATE BR",
    "ADR_NO_DATE_BR", "ADR NO/DATE/BRANCH",
]
BILL_BRANCH_CODE_COLUMN_ALIASES = [
    "BILL_BRANCH_CODE", "BILL BRANCH CODE", "BILL-BRANCH-CODE",
    "BILLBRANCHCODE", "BILL_BRANCH", "BILL BRANCH",
    "BILLING_BRANCH_CODE", "BILLING BRANCH CODE",
]

# CONFIRMED by user (2026-10-02): Month-wise ADR (Pending Billing) summary ke
# liye "ADR DATE" hi asli source-of-truth column hai -- kuch exports mein
# literally "DATE" column ke naam se aata hai (jaise jab ADR_NO_DATE_BRANCH
# ko split kiya ja chuka ho), kuch mein "ADR DATE" poora likha hota hai.
# Agar dono mein se koi na mile, to ADR_NO_DATE_BRANCH combo-column (upar
# wala) se hi date-shaped token nikal lete hain (month_summary.py karta hai).
ADR_DATE_COLUMN_ALIASES = [
    "ADR DATE", "ADR_DATE", "ADR-DATE", "ADRDATE",
    "ADR DT", "ADR_DT", "ADR MADE DATE", "A.D.R DATE", "DATE",
]
# CONFIRMED by user (2026-10-03): NET (POD ON NET BUT NOT BILLED) month-wise
# summary ke liye asli date-of-truth column "CN_DELIVERY_DATE" hai (branch
# POD_NOT_BILLED ke liye hamesha ki tarah BILL_BRANCH_CODE se hi aata hai --
# config.CATEGORY_BRANCH_COLUMN/ingest.detect_branch_column already karte
# hain, dobara likhne ki zaroorat nahi). Jis row mein CN_DELIVERY_DATE nahi
# milti, wahi "current month" popup wala case hai -- ADR jaisa hi.
NET_DATE_COLUMN_ALIASES = [
    "CN_DELIVERY_DATE", "CN DELIVERY DATE", "CN-DELIVERY-DATE",
    "CNDELIVERYDATE", "DELIVERY DATE", "DELIVERY_DATE",
    "CN_DEL_DATE", "CN DEL DATE", "DELIVERY DT",
]

# "OTHER" sheets (client-specific jaise 'VE COMMERCIAL VEHICLES LTD') mein
# ek hi sheet ke andar multiple mini-blocks hote hain, har block ka apna
# "<branch code> | <title>" header row hota hai. Title text se yeh pata
# chalta hai woh block kis category mein jayega:
OTHER_SHEET_BLOCK_TITLE_RULES = [
    ("PENDING_BILLING", ["adr made but not billed", "pending billing"]),
    ("POD_NOT_BILLED", ["pod on net", "not billed"]),
    ("DDR_PENDING", ["ddr"]),
    ("PAID", ["paid"]),
    ("TOPAY", ["to pay", "topay"]),
]

# CONFIRMED by user: 'ADR MADE BUT NOT BILLED' ko short mein sirf 'ADR' bhi
# bolte hain, 'NET POD MADE BUT NOT BILLED' ko sirf 'NET'. Kuch files mein
# marker-row ka title cell poora phrase na likh kar sirf yeh chhota alias
# likhti hain. Yeh EXACT match hote hain (substring nahi) taaki 'NET' jaisa
# chhota keyword kahin bhi galti se kisi aur cell se match na ho jaaye.
OTHER_SHEET_BLOCK_TITLE_EXACT_ALIASES = [
    ("PENDING_BILLING", ["ADR"]),
    ("POD_NOT_BILLED", ["NET"]),
]

# CONFIRMED by user (2026-10-10): NET wali sheet ka naam kabhi sirf "NET",
# kabhi "NET POD", "POD NET", "NETPOD", "NET-POD", "NET (POD)", sirf "POD",
# ya poora "NET POD MADE BUT NOT BILLED" ho sakta hai (ADR ke liye bhi aisa
# hi: "ADR", "ADR MADE", ...). Pehle sirf "NET"/"ADR" EXACT match hota tha,
# isliye baaki naam galti se party-sheet ('OTHER') ban jaate the aur NET ka
# data count hi nahi hota tha. Ab family-rule: sheet/title ke SAARE shabd
# neeche ke anchor/filler vocabulary mein hon (aur kam se kam ek anchor ho)
# to us category ka maana jaata hai -- party naam jaise "NET ONE TECHNOLOGIES"
# mein extra shabd hote hain, isliye woh galti se category nahi ban sakte.
NET_SHEET_ANCHORS = ["NET", "POD", "NETPOD", "PODNET"]
ADR_SHEET_ANCHORS = ["ADR", "ADRMADE"]
SHEET_FAMILY_FILLER_WORDS = [
    "MADE", "BUT", "NOT", "BILLED", "BILL", "BILLING", "PENDING", "ON", "OF", "THE",
]

# Header row dhundhne ke liye — is row mein yeh keyword mil jaye to wahi
# header row maan lo.
HEADER_MARKER_KEYWORDS = ["S.NO", "CN_NO", "CN NO"]
MAX_HEADER_SCAN_ROWS = 12

# --- Gemini settings (user apni sidebar se override kar sakta hai) ---
# NOTE: aapne 'gemini-3.1-flash-lite' bataya — yeh naam mere training data
# (Jan 2026 tak) mein maujood nahi hai, ho sakta hai yeh naya model ho ya
# naam mein typo ho. App mein isko sirf ek default/starting value rakha hai —
# agar API "model not found" error de to sidebar mein sahi naam daal dena
# (Google AI Studio: https://aistudio.google.com/ par model list check karo).
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# --- DDR PENDING: DLY_CHALLAN_FROM special-case rule (CONFIRMED by user) ---
# Normally DLY_CHALLAN_FROM hi branch code hota hai. Lekin agar uski value
# is threshold se bada ho (confirmed range: 5000-9999, jaise 9994), to woh
# asli branch code NAHI hai -- asli branch DLY_CHALLAN field ke shuru wale
# digits se nikalna padta hai (year-code se pehle wala hissa). Year-code
# khud current/pichhle/agle saal ke last-2-digits se auto-detect hota hai
# (kabhi '26' hardcode nahi karte, taaki agle saal bhi chale).
DDR_CHALLAN_FROM_DECODE_THRESHOLD = 5000  # > iss se bada => DLY_CHALLAN se decode karo
DDR_CHALLAN_BRANCH_LENGTHS = [4, 3, 5]     # branch-code lambai try karne ka order

# Configurable EXCLUSION engine (spec: kabhi bhi silently discard nahi karte,
# sirf FINAL summary se bahar rakhte hain, audit mein poora dikhate hain).
# CONFIRMED by user (2026-09-29): 'OTHER END' / 'DISPUTE MATTER' matters
# REPORT mein dikhte hain (apni alag tab mein) lekin SUMMARY ke kisi bhi
# category (PENDING BILLING / POD ON NET / DDR / PAID / TO PAY) ke COUNT
# mein kabhi shaamil nahi hote -- chahe yeh ek POORI ALAG SHEET ho (jaise ek
# tab ka naam hi "OTHER END" ho) YA kisi aur sheet ke ANDAR ek chhota
# block/sub-section ho (jaise ADR sheet ke beech mein "DISPUTE MATTER" wala
# mini-section) -- dono jagah yahi EK list check hoti hai (categorize_sheet()
# sheet-naam ke liye, _match_block_title_category() block-title ke liye),
# taaki naya exclusion future mein bas YAHAN add karo, kahin aur code change
# ki zaroorat nahi. Substring match hai (case-insensitive) isliye header mein
# thoda bhi spacing/hyphen/short-form farak ho, tab bhi pakda jaata hai --
# neeche jaan-boojh kar kai likhne-ke-tareeke diye hain:
EXCLUDED_SHEET_KEYWORDS = [
    "other end", "other-end", "otherend", "o/e", "o.e.", "other",
    "dispute matter", "dispute matters",
    "disputed matter", "disputed matters",
    "disp matter", "disp. matter", "disp mtr", "dispute",
]

# Directory file ke sheet naam jo hum parse karte hain (ANUJ directory jaisi
# file mein har sheet ek "department/zone" hai)
DIRECTORY_HEADER_MARKERS = ["BRANCH CODE", "EMP.\nCODE", "EMP.CODE", "EMP CODE"]
