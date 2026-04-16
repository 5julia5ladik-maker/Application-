# HomeStock Universal

Home inventory web app with AI camera, product cards, shopping plan, analytics, categories, import/export, and PWA support.

## Project Files

- `app.py` - FastAPI backend.
- `index.html` - single-file frontend app.
- `requirements.txt` - Python dependencies.
- `railway.json` - Railway deploy config.
- `Procfile` - deploy compatibility.
- `runtime.txt` - Python runtime version.
- `.env.example` - example environment variables.
- `.gitignore` - keeps secrets and local data out of GitHub.

## Local Run

Install dependencies:

```powershell
py -m pip install -r requirements.txt
```

Set Gemini API key:

```powershell
$env:GEMINI_API_KEY="your_gemini_api_key"
```

Or create a local `gemini_api_key.txt` file next to `app.py`.

Start server:

```powershell
.\start_server.bat
```

Open on PC:

```text
http://127.0.0.1:8000
```

Open on phone in the same Wi-Fi network:

```text
http://YOUR_PC_LOCAL_IP:8000
```

Example:

```text
http://192.168.0.147:8000
```

## Railway Deploy

1. Push this repository to GitHub.
2. In Railway, create `New Project`.
3. Choose `Deploy from GitHub repo`.
4. Add environment variables in Railway:

```text
GEMINI_API_KEY=your_gemini_api_key
SECRET_KEY=change-me-before-production
IMAGE_PROVIDER=pollinations
ALLOW_APPROX_TEXT_IMAGE=1
POLLINATIONS_IMAGE_MODEL=flux
POLLINATIONS_IMAGE_MODELS=flux,seedream,kontext,nanobanana
IMAGE_WIDTH=720
IMAGE_HEIGHT=520
POLLINATIONS_TIMEOUT=75
```

Optional:

```text
POLLINATIONS_API_KEY=your_pollinations_key
```

Railway uses this start command from `railway.json`:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Health check:

```text
/health
```

## Secrets

Do not commit these files:

- `gemini_api_key.txt`
- `pollinations_api_key.txt`
- `.env`
- `data/`

They are already ignored by `.gitignore`.

If an API key was shared publicly, rotate it before production deploy.

## Data Storage

The current version stores local data in `data/`. For production with registration, shared access, and stable Railway deploys, the next step is moving data to PostgreSQL, for example Railway Postgres.

## Next Version

Recommended next steps:

- user registration and login
- household groups
- PostgreSQL database
- sync between phone and PC through one hosted server
- access roles: owner, family member, read-only
