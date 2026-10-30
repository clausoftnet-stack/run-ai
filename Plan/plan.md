# Run-AI v2: Supabase + backend FastAPI + frontend Astro/React responsive, en Dokploy

## Contexto
Hoy todo son ficheros locales: `app/serve.py` (sirve ficheros + `/api/elegir`), `app/app.js`
(1.534 líneas, incluye el parser de `comidas/*.md`), un kiosko que se genera en local
(`kiosko-app/build.js`) y se publica en GitHub Pages para el Kindle, y los agentes de Claude Code
escribiendo `.md`. Objetivo: datos en Supabase con auth, un backend y un frontend robustos, una app
responsive con pantalla de resumen (lo que hoy hace el kiosko), y el Kindle con `?modo=kiosko`.
Todo migrado y probado **antes** de desplegar. Sin API de Claude: solo el plan Pro.

Decisiones tomadas: Supabase Cloud (free) · los agentes acceden con un CLI Python que habla JSON ·
Kindle en una ruta renderizada en el servidor, sin JS.

## La restricción que manda
Sin API, **ningún servidor llama a Claude**. La IA sigue siendo Claude Code interactivo en tu portátil.
Ahora los agentes ya no tocan ficheros: llaman a `py runai.py ...`, que lee y escribe en Supabase.
Ventaja: como los datos están en la nube, da igual desde dónde lances Claude Code y no hace falta SSH.
El cron de la VPS solo hace lo determinista: sincronizar Garmin y recalcular.

## Arquitectura
```
Supabase Cloud ── Postgres (+ RLS) · Auth (email/magic link) · Storage (series por segundo de raw/)
      ▲                         ▲
      │ service key             │ JWT del usuario
 ┌────┴──────────────┐    ┌─────┴──────────────────────────────┐
 │ api (FastAPI, Py) │◄───│ web (Astro SSR + islas React)      │
 │  /api/... REST    │    │  /           app responsive        │
 │  jobs: sync Garmin│    │  /?modo=kiosko&t=TOKEN  HTML sin JS│
 │  plan_comidas     │    │  /cal/*.ics  calendarios iCloud    │
 └───────────────────┘    └────────────────────────────────────┘
        ▲ cron (contenedor worker o cron de Dokploy)
 runai.py (CLI de los agentes) ──► misma capa de dominio que la api
```
- **Backend = Python/FastAPI**, no Spring Boot: reutiliza `sync_garmin.py`, `plan_comidas.py`,
  `revision.py`, `query.py` y `marca_elegida`. Una sola capa de dominio (`runai/` como paquete)
  que usan la api, el CLI y el cron.
- El frontend **solo habla con la api** (que comprueba el JWT de Supabase). RLS activado y anon sin
  permisos como segunda barrera. La service key solo vive en la api y en el cron.
- Kindle: `?modo=kiosko` lo renderiza Astro en el servidor como HTML/CSS plano, con un token propio
  de solo lectura (el Kindle no aguanta un login ni React). Si no hay token o no es válido, devuelve 404.

## Modelo de datos (migraciones SQL en `supabase/migrations/`)
- `perfil` (peso, basal, test T0) y `zonas` → tablas/jsonb; el bloque `garmin:auto` pasa a columnas que
  solo escribe el sync. Se mantiene la jerarquía: lactato T0 > Garmin, y para peso y gasto manda la báscula.
- `garmin_dia` (wellness, sueño, HRV: jsonb + columnas clave), `actividad` (resumen) y la serie por segundo en Storage.
- `entreno_planificado` (lo que viene del calendario de Garmin: título, hora, deporte, minutos).
- `instruccion_dia` (actividades/nutrición por día; sustituye a `instrucciones-semana.md`).
- `plan_dia` → `comida` (kcal y macros objetivo) → `opcion` (texto, macros, `elegida bool` +
  **índice único parcial: una sola elegida por comida**, que es el contrato del (E) puesto en la BD).
- `compra_item` (producto, cantidad, precio Mercadona, semana) y `revision` (semana, secciones jsonb).
- Prosa que no son datos (`perfil.md` narrativo, `sesiones/`, `tecnica-carrera/`) → se queda en el repo.

## CLI de los agentes: `runai.py`
`semana get <W>` · `semana set-recetas <W> < recetas.json` · `elegir <W> <dia> <comida> <n>` ·
`compra set <W> < items.json` · `revision set <W> < rev.json` · `perfil` · `zonas`.
Entrada y salida en JSON, validadas con pydantic. Si el JSON está mal, falla con un mensaje claro.
Se reescriben `.claude/agents/*.md` y `.claude/commands/*.md` para usarlo, y el hook PostToolUse pasa a
lanzar los tests del CLI en vez de `node app/test_app.js`.

## Frontend (Astro + React + Tailwind, rediseño mobile-first)
- **Resumen** (portada, inspirada en el kiosko): entreno de hoy, kcal/macros, comidas elegidas,
  readiness (sueño, HRV, carga aguda/crónica) y la semana de un vistazo.
- Pestañas: Semana/Comidas (elegir opción = tocar), Compra (checklist), Entrenos/gráficas, Revisión.
- El mismo endpoint `/api/resumen` alimenta el resumen del móvil y el kiosko del Kindle.

## Fases (cada una se cierra con tests en verde; no se despliega hasta la 6)
1. **Esqueleto**: monorepo `api/` (FastAPI + paquete de dominio), `web/` (Astro), `supabase/`
   (CLI de Supabase, BD local en Docker para desarrollar). Docker Compose local.
2. **Esquema + migraciones** y RLS. Tests con pytest contra el Supabase local.
3. **Migración de datos** (`scripts/importar.py`): lee los `.md` actuales con un port a Python del parser
   de `app/app.js` y vuelca también `data.json` y `raw/`. Comprobación: los totales de kcal y de opciones
   elegidas por semana cuadran con lo que da `node kiosko-app/build.js` sobre los mismos ficheros.
4. **Dominio + CLI + api**: `sync_garmin` escribe en la BD, `plan_comidas` lee instrucciones y entrenos
   de la BD, y se migran `revision`/`query`. Los tests actuales (`test_sync_garmin.py`, los casos de
   `marca_elegida` y los de `test_app.js`) se portan antes de mover el código.
5. **Frontend**: login, resumen, pestañas y `?modo=kiosko`. Playwright: viewport de móvil + una
   prueba del HTML del kiosko sin JS.
6. **Agentes y comandos** al CLI, y una semana real en paralelo con el sistema viejo (`/semana` completo).
7. **Despliegue en Dokploy**: VPS de ~5 €/mes, 2 apps (web, api) + cron, variables de entorno y dominio
   con HTTPS. Apuntar el Kindle a la nueva URL. Después se retiran `app/`, `kiosko-app/` y GitHub Pages.

## Avisos
- **Es mucho trabajo**: calcula varias semanas de ratos sueltos. Mientras tanto el sistema actual sigue
  siendo el que usas: no lo toques hasta la fase 6.
- Tokens del Pro: una fase por sesión, `/clear` entre fases, Sonnet para lo mecánico (portar pestañas,
  Dockerfiles) y Opus para el esquema y el port del parser.
- Free tier: 500 MB de BD y 1 GB de Storage. Si `raw/` no cabe, se queda en un volumen de la VPS.
- Garmin desde la VPS: los tokens se guardan en un volumen o en un secret; ojo al MFA la primera vez.

## Ficheros clave que se reutilizan
`app/serve.py` (`marca_elegida`), `app/app.js` (`parseSemana`, que es la referencia del port),
`app/test_app.js`, `garmin-ai/{sync_garmin,plan_comidas,revision,query,mercadona}.py`,
`kiosko-app/build.js` (forma del resumen), `kiosko-app/calendario.py` (.ics), `.claude/agents/*`, `CLAUDE.md`.

## Estructura del repo nuevo (preparado para Dokploy)
Carpeta: `C:\Proyectos\Personal\Code\Clausoft\Run-Ai` (vacía hoy). Fuente de los datos a migrar:
`C:\Proyectos\IA\run-ai`, que solo se lee y no se modifica.
```
Run-Ai/
  api/         FastAPI + paquete de dominio runai/ + Dockerfile + tests
  worker/      cron de sync de Garmin (misma imagen que api, otro comando)
  web/         Astro SSR (adapter node) + React + Dockerfile
  cli/         runai.py (lo usan los agentes)
  supabase/    config.toml + migrations/*.sql + seed
  scripts/     importar.py (migración de los datos actuales)
  .claude/     agentes y comandos adaptados
  docker-compose.yml   la usa Dokploy (tipo "Compose") y sirve también en local
  .env.example         sin secretos; el .env real no va a git
  .github/workflows/ci.yml   tests + `supabase db push` de las migraciones al hacer merge a main
```

## Qué necesito para ir solo
**GitHub**
- URL del repo (owner/nombre). `gh` está logueado como `alvaroromero-hefame` con los scopes
  repo y workflow. Si el repo es de tu cuenta personal (Sarebok85), haz `gh auth login` con esa
  cuenta o invita a la de trabajo como colaboradora.
- En Dokploy, conectar GitHub (Settings → Git → GitHub App) lo tienes que hacer tú desde la UI.

**Supabase** (en `<carpeta nueva>/.env`, nunca en el chat):
- `SUPABASE_URL`, `SUPABASE_ANON_KEY` (publishable), `SUPABASE_SERVICE_ROLE_KEY` (secret)
- `SUPABASE_PROJECT_REF`, `SUPABASE_DB_PASSWORD`
- `SUPABASE_ACCESS_TOKEN` (Account → Access Tokens): con él uso `supabase link/db push` y configuro
  Auth (Site URL, redirects, quitar el registro público) sin que tengas que tocar la consola.
- Mi recomendación: un segundo proyecto `run-ai-dev` (el free tier permite 2) para no probar contra producción.

**Máquina**: ruta de la carpeta nueva, Docker Desktop arrancado (ahora mismo está parado) y,
si no quieres ir aprobando comando a comando, permisos para npm/py/docker/supabase/git en `.claude/settings.json`.

## Dokploy en Docker Desktop (laboratorio)
Dokploy solo se instala en Linux y necesita Docker Swarm, así que en Windows va dentro de WSL2:
1. `wsl --install -d Ubuntu-24.04` y reinicia si te lo pide.
2. Docker Desktop → Settings → Resources → WSL integration → activa Ubuntu-24.04.
3. Deja libres los puertos 80, 443 y 3000 (IIS u otro servidor local suele ocupar el 80).
4. En Ubuntu: `docker swarm init --advertise-addr 127.0.0.1`
5. `curl -sSL https://dokploy.com/install.sh | sudo sh`
6. Abre http://localhost:3000 y crea el usuario admin.
7. Projects → Create → Compose → repo de GitHub → rama main → variables del `.env` → Deploy.
   Para el dominio usa el que genera `traefik.me`, o `localhost`.

Limitaciones del lab: GitHub no puede mandar webhooks a tu portátil, así que el deploy es con el
botón; el Kindle no lo verá fuera de tu red; y Swarm sobre Docker Desktop a veces da problemas de red.
Si se atasca, merece más la pena ir directo a una VPS de 5 €/mes, porque además ese es el entorno real.

## Verificación final antes de desplegar
`pytest` (dominio, api y CLI) · tests del front · Playwright móvil y kiosko · importación cuadrada
contra los `.md` · una semana entera (`/semana` → elegir en el móvil → `/compra` → `/revision`) sobre
el Supabase local.
