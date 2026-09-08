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