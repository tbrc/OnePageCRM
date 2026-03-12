import streamlit as st
import time
import requests
from requests.auth import HTTPBasicAuth
import schedule
import threading
import os
import base64
import datetime
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import re

# --- Country Mapping ---
# --- ISO 3166-1 Alpha-2 Country Codes ---
COUNTRY_MAP = {
    "Afghanistan": "AF", "Albania": "AL", "Algeria": "DZ", "Andorra": "AD", "Angola": "AO",
    "Argentina": "AR", "Armenia": "AM", "Australia": "AU", "Austria": "AT", "Azerbaijan": "AZ",
    "Bahamas": "BS", "Bahrain": "BH", "Bangladesh": "BD", "Belgium": "BE", "Belize": "BZ",
    "Benin": "BJ", "Bhutan": "BT", "Bolivia": "BO", "Bosnia and Herzegovina": "BA", "Botswana": "BW",
    "Brazil": "BR", "Brunei": "BN", "Bulgaria": "BG", "Burkina Faso": "BF", "Burundi": "BI",
    "Cambodia": "KH", "Cameroon": "CM", "Canada": "CA", "Chile": "CL", "China": "CN",
    "Colombia": "CO", "Costa Rica": "CR", "Croatia": "HR", "Cuba": "CU", "Cyprus": "CY",
    "Czech Republic": "CZ", "Denmark": "DK", "Dominican Republic": "DO", "Ecuador": "EC", "Egypt": "EG",
    "El Salvador": "SV", "Estonia": "EE", "Ethiopia": "ET", "Finland": "FI", "France": "FR",
    "Germany": "DE", "Greece": "GR", "Guatemala": "GT", "Honduras": "HN", "Hong Kong": "HK",
    "Hungary": "HU", "Iceland": "IS", "India": "IN", "Indonesia": "ID", "Iran": "IR",
    "Iraq": "IQ", "Ireland": "IE", "Israel": "IL", "Italy": "IT", "Jamaica": "JM",
    "Japan": "JP", "Jordan": "JO", "Kazakhstan": "KZ", "Kenya": "KE", "Kuwait": "KW",
    "Latvia": "LV", "Lebanon": "LB", "Lithuania": "LT", "Luxembourg": "LU", "Madagascar": "MG", "Malaysia": "MY",
    "Maldives": "MV", "Malta": "MT", "Mexico": "MX", "Monaco": "MC", "Mongolia": "MN",
    "Morocco": "MA", "Myanmar": "MM", "Nepal": "NP", "Netherlands": "NL", "New Zealand": "NZ",
    "Nigeria": "NG", "North Korea": "KP", "Norway": "NO", "Oman": "OM", "Pakistan": "PK",
    "Panama": "PA", "Paraguay": "PY", "Peru": "PE", "Philippines": "PH", "Poland": "PL",
    "Portugal": "PT", "Qatar": "QA", "Romania": "RO", "Russia": "RU", "Saudi Arabia": "SA",
    "Serbia": "RS", "Singapore": "SG", "Slovakia": "SK", "Slovenia": "SI", "South Africa": "ZA",
    "South Korea": "KR", "Spain": "ES", "Sri Lanka": "LK", "Sweden": "SE", "Switzerland": "CH",
    "Taiwan": "TW", "Thailand": "TH", "Turkey": "TR", "Ukraine": "UA", "United Arab Emirates": "AE",
    "United Kingdom": "GB", "United States": "US", "United States of America": "US", "Uruguay": "UY", "Uzbekistan": "UZ", "Venezuela": "VE",
    "Vietnam": "VN", "Zambia": "ZM", "Zimbabwe": "ZW", "Korea, North": "KP", "Korea, South": "KR"
}


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Write credentials.json and token.json from environment variables if present
if "GOOGLE_CREDS" in os.environ:
    with open("credentials.json", "w") as f:
        f.write(os.environ["GOOGLE_CREDS"])

if "GOOGLE_TOKEN" in os.environ:
    with open("token.json", "w") as f:
        f.write(os.environ["GOOGLE_TOKEN"])

# --- Gmail Setup ---
def get_gmail_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("Run OAuth flow again to regenerate token.json with gmail.modify scope")
    return build("gmail", "v1", credentials=creds)

def extract_body(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            data = part["body"].get("data")
            if data:
                decoded = base64.urlsafe_b64decode(data).decode("utf-8")
                if part["mimeType"] == "text/html":
                    return decoded
                elif part["mimeType"] == "text/plain":
                    return decoded
    elif "body" in payload and "data" in payload["body"]:
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")
    return ""

def parse_html_body(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    fields = {}
    for p in soup.find_all("p"):
        bold = p.find("b")
        if bold:
            key = bold.get_text().replace(":", "").strip()
            bold.extract()
            value = p.get_text().strip()
            fields[key] = value
    return fields

def fetch_unread_inbound(service):
    query = "label:INBOX is:unread from:inbound@tbrc.info"
    results = service.users().messages().list(userId="me", q=query).execute()
    messages = results.get("messages", [])
    structured_list = []
    if not messages:
        return structured_list
    
    for msg in messages:
        full_msg = service.users().messages().get(userId="me", id=msg["id"]).execute()
        body_html = extract_body(full_msg["payload"])
        fields = parse_html_body(body_html)

        email_value = fields.get("Email ID", "") or fields.get("Email", "")
        
        structured = {
            "Report Name": fields.get("Report Name", ""),
            "Name": fields.get("Name", ""),
            "Email ID": email_value,
            "Company Name": fields.get("Company Name", ""),
            "CountryCode": COUNTRY_MAP.get(fields.get("Country", ""), fields.get("Country", "")),
            "Phone No": fields.get("Phone No", "")
        }
        structured_list.append(structured)
        
        # Mark message as read
        service.users().messages().modify(
            userId="me",
            id=msg["id"],
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    return structured_list

def is_valid_email(email: str) -> bool:
    """Check if email is syntactically valid."""
    pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    return re.match(pattern, email) is not None

def is_junk_email(email: str) -> bool:
    """Detect junk emails by heuristics."""
    if not email:
        return True
    
    local, _, domain = email.partition("@")
    local = local.strip()
    domain = domain.lower().strip()

    # Rule 1: numeric-heavy local part (junk)
    if local.isdigit() or sum(c.isdigit() for c in local) > len(local) * 0.7:
        return True

    # Rule 2: disposable domains (expandable list)
    disposable_domains = {"qq.com", "163.com", "mailinator.com", "tempmail.com"}
    if domain in disposable_domains:
        return True

    # Rule 3: suspiciously short domain
    if len(domain.split(".")) < 2:
        return True

    return False

# --- OnePageCRM Push Function ---
def push_to_onepagecrm(fields, endpoint_user_id, api_key, owner_id):
    api_url = "https://app.onepagecrm.com/api/v3/contacts"
    payload = {
        "first_name": fields.get("Name", ""),
        "last_name": "",
        "company_name": fields.get("Company Name", ""),
        "emails": [{"type": "work", "value": fields.get("Email ID", "")}],
        "phones": [{"type": "work", "value": fields.get("Phone No", "")}],
        #"tags": [fields.get("Country", "")],
        "visibility": "private",
        "owner_id": owner_id,
        "background": fields.get("Report Name", ""),
        "address_list": [
         {
                "address": "",
                "city": "",
                "state": "",
                "zip_code": "",
                "country_code": fields.get("CountryCode", ""),
                "type": "work"
         }
        ]
    }
    response = requests.post(api_url, json=payload, auth=HTTPBasicAuth(endpoint_user_id, api_key))
    return response.status_code, response.text

# --- Workflow ---
def run_workflow(endpoint_user_id, api_key, owner_id, last_run_placeholder, recent_contacts_placeholder):
    service = get_gmail_service()
    mails = fetch_unread_inbound(service)
    results = []
    
    for fields in mails:
        email = fields.get("Email ID", "")
        
        # Skip invalid or junk emails
        if not is_valid_email(email) or is_junk_email(email):
            print(f"⏭️ Skipping junk/invalid email: {email}")
            continue
        
        status, text = push_to_onepagecrm(fields, endpoint_user_id, api_key, owner_id)
        results.append((fields, status, text))
    
    # Update UI placeholders
    if results:
        last_run_placeholder.markdown(
            f"✅ Auto run completed at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        recent_contacts_placeholder.write(results)
    
    return results

# --- Background Scheduler ---
def scheduler_loop(endpoint_user_id, api_key, owner_id, last_run_placeholder, recent_contacts_placeholder):
    while True:
        run_workflow(endpoint_user_id, api_key, owner_id, last_run_placeholder, recent_contacts_placeholder)
        time.sleep(60)  # check every 60 seconds


# --- Streamlit UI ---
def main():
    st.title("📬 Gmail → OnePageCRM Automation")

    ENDPOINT_USER_ID = os.environ.get("ONEPAGECRM_USER_ID")
    API_KEY = os.environ.get("ONEPAGECRM_API_KEY")
    OWNER_ID = os.environ.get("ONEPAGECRM_OWNER_ID")

    last_run_placeholder = st.empty()
    recent_contacts_placeholder = st.empty()

    if st.button("Manual Push"):
        results = run_workflow(ENDPOINT_USER_ID, API_KEY, OWNER_ID, last_run_placeholder, recent_contacts_placeholder)
        for fields, status in results:
            st.write("Contact:", fields)
            st.write("Status:", status)
            st.write("Response:", text)

    st.write("⏱️ This app auto-runs every hour in the background.")

    # Start scheduler in background thread
    threading.Thread(
        target=scheduler_loop,
        args=(ENDPOINT_USER_ID, API_KEY, OWNER_ID, last_run_placeholder, recent_contacts_placeholder),
        daemon=True
    ).start()

if __name__ == "__main__":
    main()








