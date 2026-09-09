# Deployment

# Vercel frontend + separate backend

Vercel hosts the React frontend for free. The FastAPI backend must run separately on a Python-capable host.

## Deploy the frontend

1. Push this repository to GitHub.
2. At https://vercel.com, choose **Add New > Project** and import the repository.
3. Set **Root Directory** to `frontend`.
4. Use these settings:
	- Framework preset: `Vite`
	- Build command: `npm run build`
	- Output directory: `dist`
5. Add the environment variable `VITE_API_URL` with your deployed backend URL, for example `https://your-backend.example.com`.
6. Deploy the project.

The Vercel URL is the frontend origin that must be added to the backend's `CORS_ORIGINS` environment variable.

## Backend requirement

The backend still needs a Python host. Set:

```text
CORS_ORIGINS=https://your-project.vercel.app
```

Then verify the backend at `/api/health` and redeploy the Vercel frontend after setting `VITE_API_URL`.

The included `frontend/vercel.json` keeps React routes working when users refresh a deep link.

## PythonAnywhere backend

The Vercel serverless function is not suitable for this API because its scientific
Python dependencies exceed Vercel's 250 MB uncompressed function limit. Deploy the
frontend and backend separately instead. The backend can continue using MongoDB Atlas;
the checked-in application no longer uses SQLite.

1. Create a PythonAnywhere account and a Python 3.11 virtual environment.
2. Open a Bash console and clone the repository, then install the backend dependencies:

```bash
cd ~/SIH-1
python3.11 -m venv ~/.virtualenvs/mplads
source ~/.virtualenvs/mplads/bin/activate
pip install -r requirements.txt
```

3. In the PythonAnywhere **Web** tab, create a manual configuration for the virtualenv
	and set the WSGI file to the repository's `pythonanywhere_wsgi.py`.
4. Edit the WSGI file's `PROJECT_ROOT` if the repository is not at `~/SIH-1`.
5. Set these environment variables in the WSGI file or the hosting environment:

```text
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>/<database>
MONGO_DB_NAME=mplads_sentinel
SEED_FROM_SAMPLE=1
CORS_ORIGINS=https://<your-project>.vercel.app
MPLADS_LIVE_HOUSE=rajya_sabha
ENVIRONMENT=production
```

The WSGI adapter starts FastAPI's lifespan, which creates indexes, loads the risk
model, and seeds an empty database from the bundled sample CSV. The in-process nightly
scheduler remains enabled on PythonAnywhere. Use the backend's `/api/health` endpoint
to verify the deployment before configuring Vercel.

The adapter is intentionally `a2wsgi.ASGIMiddleware`: `asgiref.WsgiToAsgi` wraps WSGI
applications for ASGI and would use the wrong direction for this FastAPI application.


## Local production check

```bash
cd frontend
npm ci
npm run build
```

The backend production command is:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```