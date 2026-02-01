# 🤖 Telegram Moderation Bot (Python)

Un bot de moderación para Telegram, **open-source**, enfocado en ser:
- Fácil de configurar
- Seguro
- Potente para grupos reales

Incluye sistema de warns con base de datos, banned words, auto-ban, mute, mod-log y un **menú de configuración con botones** solo para administradores.

---

## ✨ Funcionalidades

### ⚠️ Moderación
- `/warn` – Añade un warn a un usuario (por reply)
- `/unwarn` – Quita el último warn
- `/clearwarns` – Borra todos los warns
- `/warns` – Lista los warns de un usuario
- **Auto-ban** cuando se alcanza el límite de warns

### 🔇 Silencios y baneos
- `/mute <minutos>` – Silencia usuarios temporalmente
- `/ban` – Banea usuarios
- `/unban` – Quita el ban (por reply o por user_id)

### 🚫 Banned Words (palabra completa)
- Lista de palabras prohibidas **por grupo**
- Si un usuario usa una palabra prohibida:
  - 🗑️ El mensaje se borra
  - ⚠️ Se aplica warn automático
  - ⛔ Auto-ban si llega al límite

### ⚙️ Configuración con botones
Comando `/config` (solo admins):
- Ajustar límite de warns
- Administrar banned words (ver / agregar / quitar)
- Activar o desactivar mod-log
- Todo mediante **botones interactivos**

### 🧾 Mod-log
- Registro de todas las acciones:
  - warns
  - mutes
  - bans / unbans
  - banned words
- Puede enviarse al mismo grupo o a un grupo/canal separado

### 🔐 Seguridad
- Token protegido con variables de entorno (`.env`)
- Base de datos SQLite con migraciones automáticas
- Admin-only enforcement (no castiga admins)

---

## 🛠️ Requisitos

- Python **3.10+** (recomendado 3.12+)
- Un bot creado con **@BotFather**
- Permisos de administrador en el grupo:
  - Delete messages
  - Ban users
  - Restrict members

---

## 🚀 Instalación

### 1️⃣ Clonar el repositorio
```bash
git clone https://github.com/HHTecso/telegram-moderation-bot.git
cd telegram-moderation-bot

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
TELEGRAM_BOT_TOKEN=PEGA_TU_TOKEN_AQUI
python bot.py


🤝 Contribuciones

¡Las contribuciones son bienvenidas!
	•	Fork del repositorio
	•	Crea una rama (feature/nueva-funcion)
	•	Abre un Pull Request explicando el cambio

Ideas de mejoras:
	•	Anti-flood
	•	Captcha para nuevos usuarios
	•	Acciones configurables para banned words
	•	Dashboard web

⸻

📄 Licencia

Este proyecto está bajo la licencia MIT.
Puedes usarlo, modificarlo y distribuirlo libremente.

⸻

❤️ Agradecimientos

Proyecto creado con fines educativos y comunitarios.
Si lo usas o lo mejoras, ¡una estrella ⭐ siempre se agradece!
