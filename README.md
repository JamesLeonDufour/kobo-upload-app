# ⬆️ KoboToolbox Data Uploader

This is a **Streamlit web application** designed to upload or edit data in bulk for a **KoboToolbox survey project**.

It provides a user-friendly interface to connect to your Kobo account, select a project, and either upload new submissions from a template or download existing data for editing and re-uploading.

---

## 🧩 Key Features

### Two Modes

- **Upload New Submissions**  
  Generates a blank Excel template based on your project's survey form.  
  You can fill this template and upload it to create new records.

- **Edit Existing Submissions**  
  Exports your project's current data. You can edit this file and re-upload it to update existing records.  
  The app matches records using `_uuid` or `meta/instanceID`.

### 🔐 Secure Connection
Connects to the KoboToolbox API (Global, EU, or custom server) using your private API token.

### 🧠 Smart Template Generation
Automatically builds an Excel/CSV template that matches your survey's schema, including handling geopoint components.

### ✅ Data Validation
Intelligently finds the correct submission ID (`_uuid`, `meta/instanceID`, etc.) from your uploaded file for edits.

### 📊 Submission Reporting
Provides a row-by-row report of which submissions were **successful**, **failed**, and **skipped**.

---

## ⚙️ How to Run

### 1. Install Dependencies
Make sure you have Python 3.8+ installed.

```bash
pip install -r requirements.txt
```

### 2. Run the Streamlit App

```bash
streamlit run app.py
```

### 3. Use the App

1. Open the local URL (e.g., `http://localhost:8501`) in your browser.  
2. **Step 1:** Select your Kobo server (Global or EU) and paste in your API Token (found in your Kobo account settings).  
3. **Step 2:** Choose the project you want to upload/edit data for.  
4. **Step 3:** Select your operation mode ("Upload new" or "Edit existing").  
5. **Step 4:** Follow the on-screen prompts to download the template/data, fill it, and upload your completed file.  
6. **Step 5:** Click **"Submit All Rows"** and review the progress.

---

## 🧰 Requirements

The application relies on the following Python libraries:

- `streamlit`
- `pandas`
- `requests`
- `openpyxl`
- `xlrd`

---

## ⚠️ Limitations

- **No Repeat Groups:**  
  This tool does not currently process questions that are inside a `repeat_group`.
