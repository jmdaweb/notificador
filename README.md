# Notificador

Cliente de escritorio para Windows, accesible con lectores de pantalla (NVDA, JAWS, Narrador), que publica y recibe notificaciones de uno o varios servidores [ntfy](https://ntfy.sh). Está hecho con wxPython y solo usa controles nativos de Windows.

## Funciones

- **Publicar** en cualquier tema con todos los parámetros de la [API de publicación](https://docs.ntfy.sh/publish/):
  - título, mensaje y Markdown;
  - prioridad, etiquetas/emojis, URL al pulsar e icono;
  - adjunto local o por URL, con nombre de archivo;
  - hasta 3 acciones (`view`, `http`, `broadcast`, `copy`);
  - entrega programada con zona horaria;
  - reenvío por correo y llamada telefónica;
  - ID de secuencia para actualizar, marcar como leída o eliminar una notificación ya enviada;
  - caché, Firebase, UnifiedPush y plantillas.
- **Comprobación de adjuntos locales** antes de enviar. El programa consulta `/v1/account` para conocer los límites reales del servidor (tamaño por archivo y cuota restante). Si no puede, usa los límites documentados: 15 MB por archivo y 100 MB por visitante, o 2 MB y 20 MB en ntfy.sh.
- **Servidores y temas recientes** en listas desplegables.
- **Credenciales** (usuario y contraseña o token) que se pueden guardar cifradas con una contraseña maestra (AES-GCM con clave derivada por scrypt).
- **Suscripciones** a varios temas en varios servidores:
  - por WebSocket, con respaldo automático al flujo HTTP JSON;
  - con reconexión automática y recuperación de los mensajes perdidos mediante `since`.
- **Lista de notificaciones** con el contenido en un cuadro de solo lectura y botones para ejecutar sus acciones, abrir el enlace, descargar o abrir el adjunto y copiar el mensaje.
- **Centro de notificaciones de Windows**:
  - botones de acción;
  - pulsar la notificación abre su enlace o la muestra en el programa;
  - se respeta la prioridad (ver tabla).
- **Historial en disco** (SQLite), con búsqueda, filtro por suscripción, «solo no leídas» y retención configurable.
- **Servidor proxy opcional** (HTTP, HTTPS, SOCKS4 o SOCKS5, con usuario y contraseña) para todas las conexiones: suscripciones, publicaciones, acciones y descargas.
- **Portable**: todo se guarda en la carpeta `data` junto al programa.

### Prioridades en Windows

| Prioridad ntfy | Comportamiento |
|---|---|
| 1 – mínima | Sin sonido ni ventana emergente; solo aparece en el centro de notificaciones |
| 2 – baja | Sin sonido |
| 3 – normal | Comportamiento por defecto |
| 4 – alta | Permanece más tiempo en pantalla y suena el sonido de recordatorio |
| 5 – urgente | Escenario urgente (puede saltarse «No molestar» si Windows lo permite) y sonido de alarma |

En Preferencias puedes fijar una prioridad mínima para avisar, y silenciar suscripciones concretas.

## Servidor proxy

Se configura en *Herramientas → Preferencias → Servidor proxy*:

| Tipo | Comportamiento |
|---|---|
| Configuración del sistema | Opción por defecto. Usa el proxy de las opciones de Internet de Windows o de las variables `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` y `NO_PROXY` |
| Sin proxy | Conexión directa, aunque el sistema tenga un proxy configurado |
| HTTP / HTTPS | Proxy web; con HTTPS, la conexión con el proxy también va cifrada |
| SOCKS4 / SOCKS5 | Proxy SOCKS; los nombres de los servidores los resuelve el proxy (SOCKS4a y SOCKS5h) |

- El servidor (host) es obligatorio con los tipos manuales.
- El puerto, el usuario y la contraseña son opcionales. Si no se indica puerto, se usa 8080 para HTTP, 443 para HTTPS y 1080 para SOCKS.
- Con SOCKS4 solo se envía el usuario, porque el protocolo no admite contraseña.
- La contraseña del proxy se guarda en el almacén cifrado, nunca en `config.json`, así que necesita la contraseña maestra. Si al arrancar no desbloqueas las credenciales, el proxy se usa sin contraseña hasta que las desbloquees.
- El botón «Probar proxy» comprueba la conexión con el primer servidor reciente (`/v1/health`) sin guardar los cambios.
- Al aceptar, las conexiones abiertas se cierran y se vuelven a abrir con la nueva configuración.

## Uso con teclado

| Atajo | Acción |
|---|---|
| Ctrl+N | Publicar notificación |
| Ctrl+Mayús+N | Publicar en el tema de la notificación seleccionada |
| Ctrl+Intro (en el diálogo de publicación) | Enviar |
| Ctrl+U | Suscripciones |
| Ctrl+K | Servidores y credenciales |
| Ctrl+I | Estado de las conexiones |
| Ctrl+E | Elegir y ejecutar una acción de la notificación |
| Ctrl+L | Abrir el enlace |
| Ctrl+D | Descargar o abrir el adjunto |
| Ctrl+Mayús+C | Copiar el mensaje |
| Ctrl+R / Ctrl+Mayús+R | Marcar como leída / no leída |
| Ctrl+Mayús+A | Marcar todas como leídas |
| Supr | Eliminar la notificación |
| Ctrl+F | Ir al cuadro de búsqueda |
| F5 | Reconectar |
| Ctrl+H | Ocultar en el área de notificación |
| Ctrl+Q | Salir |

Detalles de la ventana principal:

- En la lista, Intro lleva al cuadro de contenido. La tecla de aplicaciones abre un menú con las acciones.
- Una notificación se marca como leída cuando permanece seleccionada 1,5 segundos con la ventana activa.
- Al cerrar la ventana, el programa sigue en el área de notificación (se puede desactivar en Preferencias).

## Ejecutar desde el código

Se necesita Python 3.10 o posterior (probado con 3.13 de 64 bits).

```bash
python -m pip install -r requirements.txt
```

```bash
python notificador.pyw
```

## Versión portable (PyInstaller)

```bash
python -m pip install -r requirements.txt
```

```bash
python build.py
```

Esto genera `dist/Notificador/` con `Notificador.exe`. La carpeta se puede copiar a cualquier sitio, incluido un USB. Al ejecutarla se crea `data/` a su lado.

## Datos y privacidad

Todo se guarda en `data/`:

| Archivo | Contenido |
|---|---|
| `config.json` | Preferencias, servidores y temas recientes |
| `credentials.vault` | Credenciales cifradas (servidores y contraseña del proxy); la contraseña maestra nunca se guarda |
| `notificador.db` | Suscripciones e historial de notificaciones |
| `attachments/` | Adjuntos descargados |
| `icons/` | Caché de iconos de notificación |
| `notificador.log` | Registro de errores |

Lo único que se escribe fuera de la carpeta es la clave `HKCU\Software\Classes\AppUserModelId\Notificador.ntfy.<id>`. Windows la necesita para que los botones de las notificaciones lleguen al programa. Se puede eliminar en *Herramientas → Eliminar el registro de Windows*, o desactivar en Preferencias.

Si olvidas la contraseña maestra, borra `data/credentials.vault` y vuelve a introducir las credenciales.

## Limitaciones conocidas

- Las acciones `broadcast` (intents de Android) se muestran pero no se pueden ejecutar en Windows.
- Con el programa cerrado, los botones de una notificación que siga en el centro de notificaciones solo funcionan si abren una URL. Las acciones `http` y `copy` necesitan que el programa esté abierto.
- Windows no muestra iconos remotos en las notificaciones de aplicaciones sin empaquetar. Por eso el programa descarga el icono (PNG o JPEG, hasta 512 KB) antes de mostrar la notificación.

## Traducciones

Las cadenas de la interfaz están marcadas con gettext (el idioma original es el español). Para traducir el programa:

1. Genera la plantilla `locale/notificador.pot`:

   ```bash
   python scripts/makepot.py
   ```

2. Tradúcela y guárdala compilada como `locale/<idioma>/LC_MESSAGES/notificador.mo`.

Por defecto el programa usa el idioma de Windows si hay traducción, y si no, el español. Para elegir otro, ve a *Herramientas → Preferencias → Idioma*: allí aparecen los idiomas con traducción instalada. El cambio se aplica al reiniciar, y el programa ofrece reiniciarse en ese momento. Los textos propios de wxPython y de los diálogos se muestran en el mismo idioma.

## Pruebas

```bash
python -m pytest
```

Las pruebas cubren:

- el análisis de mensajes y eventos;
- la construcción de peticiones de publicación (JSON y PUT con archivo);
- la validación de adjuntos frente a los límites;
- el almacén cifrado;
- la base de datos (deduplicación y sustitución por ID de secuencia);
- las URL de suscripción con `since`;
- la correspondencia entre prioridades y notificaciones de Windows.

## Licencia

Este programa es software libre: puedes redistribuirlo y modificarlo según los términos de la Licencia Pública General de GNU, versión 2, publicada por la Free Software Foundation. Consulta el archivo [LICENSE](LICENSE) para ver el texto completo.
