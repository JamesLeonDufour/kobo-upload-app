"""
KoboToolbox API helper functions.
Simplified version with cleaner logic.
"""

from __future__ import annotations
import io
import time
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlparse
import pandas as pd
import requests

from constants import API_HEADERS, SUBMISSION_HEADERS, API_V2_DATA_PAGE_SIZE
from data_utils import ensure_uuid_prefix


# -----------------------------
# URL and Auth Helpers
# -----------------------------

def normalize_kf_base(raw: str) -> str:
    """Normalize base URL"""
    if not raw:
        return raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return raw.strip().rstrip("/")


def auth_headers(token: str, for_submission: bool = False) -> Dict[str, str]:
    """Build auth headers"""
    headers = (SUBMISSION_HEADERS if for_submission else API_HEADERS).copy()
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


def kf_join(base: str, path: str) -> str:
    """Join base URL with path"""
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


# -----------------------------
# Assets and Projects
# -----------------------------

def list_assets(kf_base: str, token: str) -> List[Dict]:
    """List all survey projects (excludes library templates)"""
    url = kf_join(kf_base, "/api/v2/assets/")
    params = {"asset_type": "survey", "format": "json"}
    items = []
    
    with requests.Session() as session:
        while url:
            r = session.get(url, headers=auth_headers(token), params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            
            # Filter out templates, keep only projects
            results = data.get("results", [])
            projects = [item for item in results if item.get("kind") == "asset"]
            items.extend(projects)
            
            url = data.get("next")
            params = {}
    
    return items


def get_asset(kf_base: str, token: str, uid: str) -> Dict:
    """Get detailed asset information"""
    url = kf_join(kf_base, f"/api/v2/assets/{uid}/")
    r = requests.get(url, headers=auth_headers(token), params={"format": "json"}, timeout=30)
    r.raise_for_status()
    return r.json()


# -----------------------------
# Form ID Resolution
# -----------------------------

def list_kc_forms(kc_base: str, token: str) -> List[Dict]:
    """List deployed forms from KC"""
    url = kf_join(kc_base, "/api/v1/forms")
    r = requests.get(url, headers=auth_headers(token), timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def resolve_form_id(asset: Dict, kc_base: str, token: str) -> Optional[str]:
    """
    Resolve the correct form ID string for submissions.
    Checks asset settings, then validates against KC.
    """
    content = asset.get("content", {})
    settings = content.get("settings", {})
    form_id = settings.get("id_string")
    asset_name = asset.get("name", "(untitled)")
    
    if not form_id:
        # No id_string in asset, must look up in KC
        forms = list_kc_forms(kc_base, token)
        
        # Try exact title match
        matches = [f for f in forms if f.get("title") == asset_name and f.get("id_string")]
        if len(matches) == 1:
            return matches[0]["id_string"]
        
        # If only one form exists, use it
        if len(forms) == 1 and forms[0].get("id_string"):
            return forms[0]["id_string"]
        
        return None
    
    # Validate form_id exists in KC
    try:
        forms = list_kc_forms(kc_base, token)
        kc_ids = {f.get("id_string") for f in forms if f.get("id_string")}
        if form_id in kc_ids:
            return form_id
    except Exception:
        pass
    
    return form_id


# -----------------------------
# Data Export
# -----------------------------

def export_data(kf_base: str, token: str, asset_uid: str) -> bytes:
    """
    Create and download full data export.
    Returns Excel file bytes.
    """
    # Create export
    url = kf_join(kf_base, f"/api/v2/assets/{asset_uid}/exports/")
    payload = {
        "source": kf_join(kf_base, f"/api/v2/assets/{asset_uid}/data/"),
        "type": "xls",
        "fields_from_all_versions": True,
        "hierarchy_in_labels": True,
        "group_sep": "/",
        "lang": "_xml",
        "multiple_select": "summary"
    }
    
    r = requests.post(url, headers=auth_headers(token), json=payload, timeout=30)
    r.raise_for_status()
    export_uid = r.json().get("uid")
    
    # Poll until ready
    status_url = kf_join(kf_base, f"/api/v2/assets/{asset_uid}/exports/{export_uid}/")
    max_wait = 120
    start = time.time()
    
    while time.time() - start < max_wait:
        r = requests.get(status_url, headers=auth_headers(token), timeout=30)
        r.raise_for_status()
        data = r.json()
        
        status = data.get("status")
        if status == "complete":
            download_url = data.get("result")
            if download_url:
                # Download file
                r = requests.get(download_url, headers=auth_headers(token), timeout=120)
                r.raise_for_status()
                return r.content
            raise Exception("Export completed but no download URL")
        
        if status == "error":
            raise Exception("Export failed")
        
        time.sleep(2)
    
    raise TimeoutError("Export timed out")


# -----------------------------
# Submission Data
# -----------------------------

def fetch_submission_map(kf_base: str, token: str, asset_uid: str) -> pd.DataFrame:
    """
    Fetch map of existing submissions: _id, _uuid, meta/instanceID.
    Used for validating updates.
    """
    url = kf_join(kf_base, f"/api/v2/assets/{asset_uid}/data/")
    params = {"format": "json", "limit": API_V2_DATA_PAGE_SIZE}
    rows = []
    
    with requests.Session() as session:
        while url:
            r = session.get(url, headers=auth_headers(token), params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            
            for rec in data.get("results", []):
                instance_id = rec.get("meta/instanceID") or rec.get("_uuid")
                rows.append({
                    "_id": rec.get("_id"),
                    "_uuid": rec.get("_uuid"),
                    "meta/instanceID": instance_id
                })
            
            url = data.get("next")
            params = {}
    
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    
    # Standardize IDs
    df["meta/instanceID"] = df["meta/instanceID"].apply(ensure_uuid_prefix)
    return df.drop_duplicates(subset=["meta/instanceID"]).reset_index(drop=True)


# -----------------------------
# Submit Submissions
# -----------------------------

def post_submission(kc_base: str, token: str, xml_bytes: bytes) -> Tuple[bool, str]:
    """
    Post XML submission to KC.
    Returns (success: bool, message: str).
    """
    url = kf_join(kc_base, "/submission")
    
    try:
        files = {
            "xml_submission_file": ("submission.xml", io.BytesIO(xml_bytes), "text/xml")
        }
        r = requests.post(
            url,
            headers=auth_headers(token, for_submission=True),
            files=files,
            timeout=60
        )
        
        # Success codes
        if r.status_code in (200, 201, 202):
            return True, "Submitted successfully"
        
        # Duplicate (already exists - also success)
        if r.status_code == 409:
            return True, "Duplicate (already exists)"
        
        # Error
        try:
            error_msg = str(r.json())
        except Exception:
            error_msg = r.text[:500]
        
        return False, f"{r.status_code}: {error_msg}"
    
    except Exception as e:
        return False, f"Submission error: {str(e)}"