# Run-AI v2

Migración de `C:\Proyectos\IA\run-ai` (ficheros locales) a Supabase + FastAPI + Astro/React,
desplegable en Dokploy. El plan completo y las fases están en `Plan/plan.md`.
El proyecto viejo solo se lee: no se edita desde aquí.

## Piezas
- `api/` FastAPI. El dominio va en `api/runai/` y lo comparten la api, el CLI y el cron.
  `cd api && .venv/Scripts/python -m pytest`
- `web/` Astro (SSR, adapter node) + React. Requiere Node >= 22.23.4.
- `supabase/` config y migraciones. BD local: `npx supabase start` (necesita Docker Desktop).
- `docker-compose.yml`: el mismo fichero para local y para Dokploy.
- `scripts/importar.py --origen C:/Proyectos/IA/run-ai`: migración de los ficheros viejos a la
  BD (recarga completa y se verifica contra el parser de `app/app.js`).

## Reglas
- Secretos solo en `.env` (no va a git). Nunca en el chat ni en el código.
- La IA no corre en el servidor (no hay API): los agentes de Claude Code usan el CLI.
- Las cifras (peso, zonas, kcal) se leen de la BD, nunca se escriben a mano.
