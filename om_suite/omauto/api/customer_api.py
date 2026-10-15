# ==========================================
# OM Automation V2
# api/customer_api.py
# ==========================================

from bs4 import BeautifulSoup

from omauto.api.session import http
from omauto import config

import traceback
import requests
from urllib.parse import urlsplit


class CustomerAPI:

    def __init__(self):
        pass

    def fetch(self, party_code):

        if party_code is None or str(party_code).strip() == "":
            return {
                "success": False,
                "message": "Party Code Missing",
                "data": None
            }

        try:
            # Same request the portal's own search form sends (all fields, browser-like headers). The shared
            # session headers (Accept: json, Origin/Referer = ops.omone.in) belong to the CN API, not this portal.
            payload = {
                "cname": "", "ccode": str(party_code).strip(), "lcode": "", "blcode": "",
                "mcode": "", "custgst": "", "submit": "Search"
            }
            parts = urlsplit(config.CUSTOMER_API)
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "Cache-Control": "max-age=0",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": f"{parts.scheme}://{parts.netloc}",
                "Referer": config.CUSTOMER_API,
                "Upgrade-Insecure-Requests": "1",
            }

            try:
                response = http.post(config.CUSTOMER_API, data=payload, headers=headers)

            except requests.exceptions.RequestException as e:
                return {
                    "success": False,
                    "message": f"Network Error: {e}",
                    "data": None
                }

            if response.status_code != 200:
                return {
                    "success": False,
                    "message": f"HTTP {response.status_code}",
                    "data": None
                }

            soup = BeautifulSoup(response.text, "html.parser")

            table = soup.find("table")

            if table is None:
                if "no data found" in response.text.lower():
                    msg = "Customer Not Found"
                else:        # not the portal's result page at all (login page / access blocked / error page)
                    title = soup.title.get_text(strip=True)[:60] if soup.title else ""
                    msg = ("Portal ne expected result page nahi diya"
                           + (f" (page: '{title}')" if title else "")
                           + " - login/access ya network check karein.")
                return {
                    "success": False,
                    "message": msg,
                    "data": None
                }

            rows = table.find_all("tr")

            if len(rows) < 2:
                return {
                    "success": False,
                    "message": "Customer Not Found",
                    "data": None
                }

            # This portal's row 0 is NOT a reliable label row (blank/
            # inconsistent header text), so matching by lower-cased
            # header text silently returned nothing for most fields.
            # The verified-working approach reads row 1 by fixed
            # column position instead - same layout every time.
            td = rows[1].find_all("td")

            if len(td) < 22:
                return {
                    "success": False,
                    "message": "Unexpected Customer Page Layout",
                    "data": None
                }

            # Sanity check: make sure this row is actually for the
            # party code we searched, not a stale/default row the
            # page happened to be showing.
            if td[0].get_text(strip=True) != str(party_code).strip():
                return {
                    "success": False,
                    "message": "Customer Row Mismatch",
                    "data": None
                }

            result = {
                "Party Code": td[0].get_text(strip=True),
                "Party Name": td[1].get_text(strip=True),
                "Address": td[2].get_text(strip=True),
                "Booking Location": td[3].get_text(strip=True),
                "Acc Location": td[4].get_text(strip=True),
                "Bill Location": td[5].get_text(strip=True),
                "Verify": td[6].get_text(strip=True),
                "Customer Type": td[7].get_text(strip=True),
                # "GST" holds the customer's GSTIN as registered against
                # this party code - this is what feeds gst_api.fetch()
                # downstream. It was missing entirely before, which is
                # why GST/Trade Name/Legal Name etc. always came back empty.
                "GST": td[8].get_text(strip=True),
                "Customer Status": td[9].get_text(strip=True),
                "Master Code": td[10].get_text(strip=True),
                "Vendor Code": td[14].get_text(strip=True),
                "Mobile": td[15].get_text(strip=True),
                "Entered By": td[20].get_text(strip=True),
                "Entry Date": td[21].get_text(strip=True),
            }

            return {
                "success": True,
                "message": "Success",
                "data": result
            }

        except Exception as e:
            traceback.print_exc()
            return {
                "success": False,
                "message": str(e),
                "data": None
            }


customer_api = CustomerAPI()
