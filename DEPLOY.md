# Deploying to Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, pick this repo.
   Nixpacks auto-detects Python from `requirements.txt` and runs the `Procfile`.
3. In the service **Variables**, add:
   - `APP_PASSWORD` = your chosen shared password (required; the app refuses to
     serve without it).
4. Railway assigns `$PORT`; the `Procfile` already binds Streamlit to it on
   `0.0.0.0`. Under **Settings → Networking**, generate a public domain.
5. Open the domain on the iPad in Safari, enter the password, and draw with the
   Apple Pencil.

## Notes
- Free-session state is in-browser only; export the ZIP / `annotations.json`
  before closing the tab (no server-side autosave).
- To run locally the same way Railway does:
  `PORT=8501 APP_PASSWORD=test streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
