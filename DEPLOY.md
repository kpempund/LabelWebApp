# Deploying to Streamlit Community Cloud

## One-click deploy (recommended)

1. Push this repo to **GitHub**.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **"New app"**, select this repo, leave branch as `main` and file as `app.py`.
4. In the **"Advanced settings"** → **"Secrets"**, add:
   ```
   APP_PASSWORD = "your-shared-password"
   ```
5. Click **Deploy**.

Your app will be live at `https://<your-app-name>.streamlit.app` in about 2 minutes.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
APP_PASSWORD=test streamlit run app.py
```

## Notes

- Free-session state is in-browser only; export the ZIP / `annotations.json` before closing the tab.
- The password gate (`APP_PASSWORD`) is required — the app refuses to serve without it.
- The app is stateless on the server side. All annotations live in the browser session.

---

## Alternative: Railway (Docker-based)

1. Push to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, pick this repo.
3. Add `APP_PASSWORD` to the service **Variables**.
4. Railway picks up the `Procfile` automatically.