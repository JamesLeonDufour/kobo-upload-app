from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import streamlit as st

# Local imports
from constants import SERVER_PRESETS
from data_utils import (
    build_template_df,
    to_excel_bytes,
    read_table,
    normalize_update_ids,
    build_existing_id_set,
    row_to_xml,
    get_submission_id
)
from kobo_api import (
    normalize_kf_base,
    list_assets,
    get_asset,
    resolve_form_id,
    fetch_submission_map,
    post_submission,
    export_data
)


@dataclass
class AppConfig:
    """Centralized app configuration"""
    kf_base: str
    kc_base: str
    token: str
    asset_uid: str
    form_id: str
    mode: str
    asset_detail: dict
    schema_map: dict
    template_df: pd.DataFrame


def init_session_state():
    """Initialize session state with defaults"""
    defaults = {
        'config': None,
        'uploaded_df': None,
        'submit_triggered': False
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_upload_state():
    """Clear uploaded data when switching projects/modes"""
    st.session_state.uploaded_df = None
    st.session_state.submit_triggered = False


def configure_sidebar() -> Optional[AppConfig]:
    """Handle all sidebar configuration and return AppConfig if complete"""
    with st.sidebar:
        st.header("Configuration")
        
        # Step 1: Server and Token
        st.markdown("### Step 1: Server and Token")
        server_choice = st.radio("Choose server:", 
                                list(SERVER_PRESETS.keys()) + ["Other"], 
                                horizontal=True)
        
        if server_choice in SERVER_PRESETS:
            kf_base = SERVER_PRESETS[server_choice]["kf"]
            kc_base = SERVER_PRESETS[server_choice]["kc"]
        else:
            kf_base = normalize_kf_base(st.text_input("KF base URL", "https://kf.kobotoolbox.org"))
            kc_base = normalize_kf_base(st.text_input("KC base URL", "https://kc.kobotoolbox.org"))
        
        token = st.text_input("API Token", type="password", help="Found in Account Settings → Security")
        
        if not (kf_base and kc_base and token):
            st.warning("Enter server details and API token to continue.")
            return None
        
        # Step 2: Select Project
        st.divider()
        st.markdown("### Step 2: Select Project")
        
        try:
            assets = list_assets(kf_base, token)
        except Exception as e:
            st.error(f"❌ Failed to load projects: {e}")
            return None
        
        if not assets:
            st.warning("No survey projects found.")
            return None
        
        asset_opts = {f"{a.get('name','(no name)')} — {a.get('uid','')}": a for a in assets}
        choice = st.selectbox("Choose project:", list(asset_opts.keys()),
                            on_change=reset_upload_state, index=None,
                            placeholder="Select a project...")
        
        if not choice:
            st.info("Select a project to continue.")
            return None
        
        asset_uid = asset_opts[choice]['uid']
        
        # Load asset details and resolve form ID
        try:
            asset_detail = get_asset(kf_base, token, asset_uid)
            form_id = resolve_form_id(asset_detail, kc_base, token)
        except Exception as e:
            st.error(f"Failed to load project: {e}")
            return None
        
        if not form_id:
            st.error("❌ Could not determine form ID.")
            return None
        
        st.success("✅ Project Loaded!")
        st.caption(f"Form ID: `{form_id}`")
        
        # Step 3: Operation Mode
        st.divider()
        st.markdown("### Step 3: Choose Operation")
        mode = st.radio("What do you want to do?",
                       ["Upload new submissions", "Edit existing submissions"],
                       horizontal=True, on_change=reset_upload_state)
        
        # Build template and schema
        template_df, schema_map = build_template_df(asset_detail)
        
        return AppConfig(
            kf_base=kf_base,
            kc_base=kc_base,
            token=token,
            asset_uid=asset_uid,
            form_id=form_id,
            mode=mode,
            asset_detail=asset_detail,
            schema_map=schema_map,
            template_df=template_df
        )


def handle_file_upload(config: AppConfig) -> Optional[pd.DataFrame]:
    """Handle file upload for both new and edit modes"""
    is_new_mode = config.mode == "Upload new submissions"
    
    if is_new_mode:
        st.subheader("📋 New Submissions")
        st.markdown("**Instructions:** 1. Download template. 2. Fill offline. 3. Upload below.")
        
        xlsx_bytes = to_excel_bytes(config.template_df)
        st.download_button("📥 Download Template", xlsx_bytes,
                         f"{config.form_id}_template.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         use_container_width=True)
        
        uploaded = st.file_uploader("📤 Upload completed template",
                                   type=["csv", "xls", "xlsx"])
    else:
        st.subheader("✏️ Edit Existing Submissions")
        st.markdown("**Instructions:** 1. Download current data. 2. Edit offline. 3. Upload.")
        
        # Export button
        if st.button("📥 Download Current Data", type="secondary"):
            with st.spinner("Preparing export..."):
                try:
                    blob = export_data(config.kf_base, config.token, config.asset_uid)
                    st.download_button("📥 Download Export", blob,
                                     f"{config.form_id}_export.xlsx",
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                     use_container_width=True)
                except Exception as e:
                    st.error(f"Export failed: {e}")
        
        st.divider()
        uploaded = st.file_uploader("📤 Upload edited file",
                                   type=["csv", "xls", "xlsx"],
                                   help="Blanks will clear existing data")
    
    # Process uploaded file
    if uploaded is None:
        return None
    
    try:
        expected_cols = list(config.template_df.columns) if is_new_mode else None
        df = read_table(uploaded, expected_cols)
        
        if not is_new_mode:
            df, norm_info = normalize_update_ids(df)
            msg_parts = []
            if norm_info.get('created_from_uuid'):
                msg_parts.append("created instanceID")
            if norm_info.get('standardized_prefix'):
                msg_parts.append("standardized prefix")
            msg = f" ({', '.join(msg_parts)})" if msg_parts else ""
            st.success(f"✅ Loaded {len(df)} rows{msg}")
        else:
            st.success(f"✅ Ready to submit {len(df)} rows")
        
        return df
    
    except Exception as e:
        st.error(f"❌ Failed to read file: {e}")
        return None


def process_submissions(config: AppConfig, df: pd.DataFrame):
    """Process and submit all rows"""
    is_edit = config.mode == "Edit existing submissions"
    
    # Fetch existing IDs for edit mode
    existing_ids = set()
    id_map = None
    if is_edit:
        with st.spinner("Fetching existing submissions..."):
            try:
                id_map = fetch_submission_map(config.kf_base, config.token, config.asset_uid)
                existing_ids = build_existing_id_set(id_map)
            except Exception as e:
                st.error(f"Failed to fetch existing IDs: {e}")
                return
    
    # Process rows
    st.header("📊 Submission Progress")
    progress_bar = st.progress(0)
    status_text = st.empty()
    results = []
    
    for i, row in df.iterrows():
        progress_bar.progress((i + 1) / len(df))
        status_text.text(f"Processing row {i+1} / {len(df)}...")
        
        try:
            # Validate for edit mode
            deprecated_id = None
            if is_edit:
                deprecated_id = get_submission_id(row, id_map)
                if not deprecated_id:
                    results.append({"row": i+1, "status": "❌ Skipped", "detail": "No target ID"})
                    continue
                if deprecated_id not in existing_ids:
                    results.append({"row": i+1, "status": "❌ Skipped", 
                                  "detail": f"ID not found: {deprecated_id.split(':')[-1][:8]}..."})
                    continue
            
            # Build and submit XML
            xml = row_to_xml(row, config.form_id, config.schema_map, deprecated_id)
            
            # Show XML preview for first row
            if i == 0:
                with st.expander(f"📄 XML Preview (Row 1)", expanded=False):
                    st.code(xml.decode("utf-8", errors='ignore'), language="xml")
            
            ok, msg = post_submission(config.kc_base, config.token, xml)
            results.append({
                "row": i + 1,
                "status": "✅ OK" if ok else "❌ FAIL",
                "detail": msg
            })
        
        except Exception as e:
            results.append({
                "row": i + 1,
                "status": "❌ ERROR",
                "detail": f"Error: {str(e)[:100]}"
            })
    
    # Display results
    progress_bar.empty()
    status_text.empty()
    
    st.subheader("Submission Summary")
    if not results:
        st.warning("No rows were processed.")
        return
    
    res_df = pd.DataFrame(results)
    success = res_df['status'].str.contains("OK|Success", regex=True).sum()
    failed = res_df['status'].str.contains("FAIL|ERROR", regex=True).sum()
    skipped = res_df['status'].str.contains("Skipped", regex=True).sum()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Rows Processed", len(res_df))
    col2.metric("✅ Success", success)
    col3.metric("❌ Failed", failed)
    col4.metric("⏭️ Skipped", skipped)
    
    st.download_button("📥 Download Report",
                      res_df.to_csv(index=False).encode("utf-8-sig"),
                      "submission_report.csv", "text/csv",
                      use_container_width=True)
    
    if success > 0 and failed == 0 and skipped == 0:
        st.success("🎉 All rows submitted successfully!")
    elif success > 0:
        st.warning("⚠️ Some rows failed or were skipped. Check the report.")
    else:
        st.error("❌ No rows submitted successfully. Check the report.")


# -----------------------------
# Main App
# -----------------------------

st.set_page_config(page_title="Kobo Upload Tool", layout="wide")
st.title("⬆️ KoboToolbox Data Uploader")

init_session_state()

# Configure via sidebar
config = configure_sidebar()
if not config:
    st.stop()

# Main workflow
col1, col2 = st.columns(2)

with col1:
    st.header("Step 1: Prepare Your Data")
    uploaded_df = handle_file_upload(config)
    
    # Show preview for edit mode
    if config.mode == "Edit existing submissions" and uploaded_df is not None:
        with st.expander("📊 Preview (first 10 rows)", expanded=False):
            st.dataframe(uploaded_df.head(10), use_container_width=True)

with col2:
    st.header("Step 2: Submit to KoboToolbox")
    
    if uploaded_df is not None and not uploaded_df.empty:
        valid_rows = uploaded_df.dropna(how='all')
        st.success(f"✅ Ready to submit **{len(valid_rows)}** row(s)")
        submit_enabled = len(valid_rows) > 0
    else:
        st.info("ℹ️ Upload data in Step 1 to enable submission.")
        submit_enabled = False
    
    if st.button("🚀 Submit All Rows", type="primary", 
                disabled=not submit_enabled, use_container_width=True):
        st.session_state.submit_triggered = True

# Results area
st.divider()

if st.session_state.submit_triggered and uploaded_df is not None:
    valid_df = uploaded_df.dropna(how="all").reset_index(drop=True)
    if not valid_df.empty:
        process_submissions(config, valid_df)
    st.session_state.submit_triggered = False

# Footer
st.divider()
st.caption("""
**Notes:**
- Does not process repeat groups.
""")
st.caption("Version 1.0 | KoboToolbox Upload Tool")
