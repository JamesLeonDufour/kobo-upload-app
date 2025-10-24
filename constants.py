"""
Constants and configuration for the Kobo Upload Tool.
"""

# Headers for API requests (KF)
API_HEADERS = {"Accept": "application/json", "User-Agent": "KoboUploadTool/1.0"}

# Headers for data submissions (KC)
SUBMISSION_HEADERS = {"User-Agent": "KoboUploadTool/1.0"}

# Page size for paginated v2 data API
API_V2_DATA_PAGE_SIZE = 1000

# Server presets for easy selection
SERVER_PRESETS = {
    "EU": {"kf": "https://eu.kobotoolbox.org", "kc": "https://kc-eu.kobotoolbox.org"},
    "Global": {"kf": "https://kf.kobotoolbox.org", "kc": "https://kc.kobotoolbox.org"},
}