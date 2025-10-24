"""
Data and XML utility functions for the Kobo Upload Tool.
Simplified version with cleaner logic.
"""

from __future__ import annotations
import io
import uuid
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple, Optional
import pandas as pd


# -----------------------------
# ID Handling
# -----------------------------

def ensure_uuid_prefix(x: str) -> str:
    """Ensure string has uuid: prefix"""
    if not x:
        return x
    s = str(x).strip()
    return s if s.startswith("uuid:") else f"uuid:{s}"


def build_existing_id_set(id_map_df: pd.DataFrame) -> set[str]:
    """Build set of existing IDs (with and without prefix for matching)"""
    if id_map_df is None or id_map_df.empty:
        return set()
    
    ids = set()
    for val in id_map_df["meta/instanceID"].dropna():
        val_str = str(val).strip()
        ids.add(ensure_uuid_prefix(val_str))
        ids.add(val_str.replace("uuid:", ""))
    return ids


def get_submission_id(row: pd.Series, id_map_df: Optional[pd.DataFrame] = None) -> Optional[str]:
    """Extract submission ID from row for updates"""
    # Try direct columns first
    for col in ["meta/instanceID", "_uuid"]:
        if pd.notna(val := row.get(col)):
            return ensure_uuid_prefix(str(val).strip())
    
    # Try mapping from _id
    if pd.notna(row.get("_id")) and id_map_df is not None and not id_map_df.empty:
        matches = id_map_df[id_map_df["_id"] == row["_id"]]
        if len(matches) == 1:
            return ensure_uuid_prefix(str(matches.iloc[0]["meta/instanceID"]))
    
    return None


# -----------------------------
# Schema Processing
# -----------------------------

def flatten_survey(survey: List[Dict], choices: List[Dict]) -> List[Tuple[str, Dict]]:
    """Flatten survey structure into column paths"""
    cols: List[Tuple[str, Dict]] = []
    stack: List[str] = []
    choice_map = {c.get("list_name"): c.get("children", []) for c in choices if "list_name" in c}
    
    for q in survey:
        qtype = q.get("type", "")
        
        # Handle groups
        if qtype in ("begin_group", "end_group", "begin_repeat", "end_repeat"):
            if qtype.startswith("begin"):
                stack.append(q.get("name", "group"))
            elif stack:
                stack.pop()
            continue
        
        name = q.get("name")
        if not name or str(name).startswith("_"):
            continue
        
        path = "/".join(stack + [name])
        
        # Handle geopoint (split into components)
        if qtype == "geopoint":
            for suffix in ["_latitude", "_longitude", "_altitude", "_precision"]:
                cols.append((f"{path}{suffix}", q))
        
        # Handle select_multiple (single column only)
        elif qtype == "select_multiple":
            q_copy = q.copy()
            q_copy["_choice_list"] = q.get("select_from_list_name")
            cols.append((path, q_copy))
        
        # All other question types
        else:
            cols.append((path, q))
    
    return cols


def build_template_df(asset: Dict) -> Tuple[pd.DataFrame, Dict]:
    """Build template dataframe and schema map from asset"""
    content = asset.get("content", {})
    survey = content.get("survey", [])
    choices = content.get("choices", [])
    
    flattened = flatten_survey(survey, choices)
    headers = [c for c, _ in flattened]
    schema_map = {path: q for path, q in flattened}
    
    # Create template with system columns
    template = pd.DataFrame(columns=headers)
    for col in ["meta/instanceID", "_uuid", "_id"]:
        if col not in template.columns:
            template[col] = None
    
    return template, schema_map


# -----------------------------
# XML Generation
# -----------------------------

def ensure_nested(root: ET.Element, path: List[str]) -> ET.Element:
    """Ensure nested XML path exists"""
    node = root
    for p in path:
        child = node.find(p)
        if child is None:
            child = ET.SubElement(node, p)
        node = child
    return node


def row_to_xml(row: pd.Series, form_id: str, schema_map: Dict, 
               deprecated_id: Optional[str] = None) -> bytes:
    """
    Build OpenRosa XML from row data.
    If deprecated_id is provided, KC will update that submission.
    """
    root = ET.Element(form_id, {"id": form_id})
    
    for path, q_info in schema_map.items():
        value = None
        qtype = q_info.get("type")
        
        # Handle geopoint
        if qtype == "geopoint":
            lat = str(row.get(f"{path}_latitude", "")).strip()
            lon = str(row.get(f"{path}_longitude", "")).strip()
            if lat and lon:
                alt = str(row.get(f"{path}_altitude", "0")).strip()
                acc = str(row.get(f"{path}_precision", "0.0")).strip()
                value = f"{lat} {lon} {alt} {acc}"
        
        # Handle select_multiple (space-separated values)
        elif qtype == "select_multiple":
            if path in row and isinstance(row[path], str):
                # Clean and normalize: accept comma or space separated
                tokens = [t.strip() for t in row[path].replace(",", " ").split() if t.strip()]
                if tokens:
                    value = " ".join(tokens)
        
        # Handle all other types
        else:
            if path in row and pd.notna(val := row[path]):
                val_str = str(val).strip()
                if val_str:
                    value = val_str
        
        # Add to XML if we have a value
        if value is not None:
            parts = path.split("/")
            parent = ensure_nested(root, parts[:-1])
            ET.SubElement(parent, parts[-1]).text = value
    
    # Add meta/instanceID
    meta = root.find("meta") or ET.SubElement(root, "meta")
    instance_id = meta.find("instanceID") or ET.SubElement(meta, "instanceID")
    instance_id.text = f"uuid:{uuid.uuid4()}"
    
    # Add deprecatedID for updates
    if deprecated_id:
        ET.SubElement(meta, "deprecatedID").text = ensure_uuid_prefix(deprecated_id)
    
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# -----------------------------
# File I/O
# -----------------------------

def read_table(file_obj, expected_cols: Optional[List[str]] = None) -> pd.DataFrame:
    """Read CSV, XLS, or XLSX file"""
    name = (getattr(file_obj, "name", "") or "").lower()
    usecols = expected_cols if expected_cols else None
    
    def filter_cols(df: pd.DataFrame) -> pd.DataFrame:
        if expected_cols:
            keep = [c for c in df.columns if c in expected_cols]
            return df[keep]
        return df
    
    # CSV
    if name.endswith(".csv") or not any(name.endswith(ext) for ext in (".xls", ".xlsx")):
        try:
            df = pd.read_csv(file_obj, dtype=str, usecols=usecols, encoding="utf-8-sig")
        except UnicodeDecodeError:
            file_obj.seek(0)
            df = pd.read_csv(file_obj, dtype=str, usecols=usecols, encoding="latin-1")
        return filter_cols(df)
    
    # XLSX
    if name.endswith(".xlsx"):
        df = pd.read_excel(file_obj, dtype=str, usecols=usecols, engine="openpyxl")
        return filter_cols(df)
    
    # XLS (legacy)
    if name.endswith(".xls"):
        try:
            df = pd.read_excel(file_obj, dtype=str, usecols=usecols, engine="xlrd")
            return filter_cols(df)
        except Exception as e:
            raise RuntimeError(
                "Legacy .xls format not supported. Please save as .xlsx or CSV."
            ) from e
    
    # Fallback to CSV
    df = pd.read_csv(file_obj, dtype=str, usecols=usecols, encoding="utf-8-sig")
    return filter_cols(df)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert dataframe to Excel bytes"""
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Template")
    return bio.getvalue()


# -----------------------------
# Data Normalization
# -----------------------------

def normalize_update_ids(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """
    Ensure meta/instanceID column exists for updates.
    Returns (df, info_dict).
    """
    info = {"created_from_uuid": False, "standardized_prefix": False}
    
    # Clean column names
    df.columns = [c.strip() for c in df.columns]
    
    # Create meta/instanceID if missing
    if "meta/instanceID" not in df.columns:
        candidates = [
            "meta_instanceID", "meta/instanceid", "instanceID", "instance_id",
            "_uuid", "__uuid", "uuid", "submission_uuid", "_submission__uuid"
        ]
        for cand in candidates:
            if cand in df.columns:
                df["meta/instanceID"] = df[cand].apply(
                    lambda x: ensure_uuid_prefix(x) if pd.notna(x) else x
                )
                info["created_from_uuid"] = True
                break
    
    # Standardize prefix
    if "meta/instanceID" in df.columns:
        df["meta/instanceID"] = df["meta/instanceID"].apply(ensure_uuid_prefix)
        info["standardized_prefix"] = True
    
    return df, info