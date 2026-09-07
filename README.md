# Picker Hunt

App interna para coordinar el picking de items entre shoppers, buscadores,
subgerentes y admin en tiendas Walmart (soporta locales **929** y **96**,
completamente aislados entre sí).

## Stack

- FastAPI + Jinja2 + SQLAlchemy
- Postgres (Neon) en producción, SQLite local para desarrollo
- Sesiones stateless (cookies firmadas con `itsdangerous`) — sin estado en
  servidor, apto para hosting serverless
- Actualizaciones en vivo por **polling** (cada 8s), sin WebSocket ni tareas
  de background — necesario porque Vercel es serverless
- Limpieza diaria de fotos (22:00 hora Chile) vía Vercel Cron — ver
  `routers/cron.py` y la sección "Deploy a producción"

## Setup local

```bash
uv venv
uv pip install -r requirements.txt --index-url https://pypi.ci.artifacts.walmart.com/artifactory/api/pypi/external-pypi/simple --allow-insecure-host pypi.ci.artifacts.walmart.com
cp .env.example .env   # opcional: sin DATABASE_URL usa SQLite local automáticamente
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8765
```

Abrir http://localhost:8765

### Usuarios de prueba (SQLite local, se crean solos al arrancar)

| Usuario   | Password   | Tienda | Rol   |
|-----------|-----------|--------|-------|
| admin     | admin123  | 929    | admin |
| admin96   | admin96   | 96     | admin |

Crea usuarios `buscador`/`shopper`/`subgerente` desde `/admin/users` una vez
loggeado como admin.

## Deploy a producción

1. Crear proyecto en [Neon](https://neon.tech), correr `neon_schema.sql`
   contra tu base (Neon SQL Editor o `psql "$DATABASE_URL" -f neon_schema.sql`).
   Usá el connection string "pooled" (host con sufijo `-pooler`) para
   `DATABASE_URL`, ya que Vercel es serverless.
2. Conectar el repo de GitHub a [Vercel](https://vercel.com).
3. Configurar variables de entorno en Vercel: `DATABASE_URL`, `SECRET_KEY`,
   `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `APP_URL`,
   `CRON_SECRET`.
4. Deploy. Los admins (929 / 96) se crean solos en el primer arranque.

### Limpieza automática de fotos

Vercel Cron pega todos los días a las 22:00 hora Chile (01:00 UTC) a
`/cron/clear-photos`, que borra `image_data`/`image_mime` de todos los items
activos (mantiene los registros, solo tira las fotos). Está declarado en
`vercel.json` → `crons`. Requiere la env var `CRON_SECRET` seteada en Vercel
(mismo valor que en tu `.env`) — Vercel manda ese secreto solo en el header
`Authorization` de cada invocación de cron, así nadie más puede pegarle al
endpoint. Si alguna vez la app corre fuera de Vercel, este cron no dispara
solo — habría que pegarle a mano o agregar otro scheduler.

Ver `.env.example` para el detalle de cada variable.
