# Picker Hunt

App interna para coordinar el picking de items entre shoppers, buscadores,
subgerentes y admin en tiendas Walmart (soporta locales **929** y **96**,
completamente aislados entre sí).

## Stack

- FastAPI + Jinja2 + SQLAlchemy
- Postgres (Supabase) en producción, SQLite local para desarrollo
- Sesiones stateless (cookies firmadas con `itsdangerous`) — sin estado en
  servidor, apto para hosting serverless
- Actualizaciones en vivo por **polling** (cada 8s), sin WebSocket ni tareas
  de background — necesario porque Vercel es serverless

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

1. Crear proyecto en [Supabase](https://supabase.com), correr
   `supabase_schema.sql` en el SQL editor.
2. Conectar el repo de GitHub a [Vercel](https://vercel.com).
3. Configurar variables de entorno en Vercel: `DATABASE_URL`, `SECRET_KEY`,
   `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `APP_URL`.
4. Deploy. Los admins (929 / 96) se crean solos en el primer arranque.

Ver `.env.example` para el detalle de cada variable.
