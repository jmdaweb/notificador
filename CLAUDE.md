# CLAUDE.md

Notificador es un cliente de escritorio para Windows, hecho con wxPython y accesible con lectores de pantalla (NVDA, JAWS, Narrador), que publica y recibe notificaciones de servidores ntfy. La documentación de usuario está en `README.md`. La licencia es la GPL v2.

## Comandos

```bash
python -m pip install -r requirements.txt   # todas las dependencias, incluidas pytest y pyinstaller
python notificador.pyw                      # ejecutar desde el código (los datos van a ./data, ignorado por git)
python -m pytest                            # pruebas
python build.py                             # versión portable en dist/Notificador (PyInstaller onedir)
python scripts/makepot.py                   # regenerar locale/notificador.pot
```

Ejecuta `makepot.py` siempre que añadas o cambies cadenas `_()` y versiona el `.pot` en el mismo commit.

## Arquitectura

- `notificador/app.py`: `Controller` une configuración, almacenamiento, red, toasts e interfaz. `main()` instala gettext antes de crear `NotificadorApp`. `OnInit` crea el `wx.Locale` antes que cualquier ventana. El reinicio (cambio de idioma) lo hace `main()` tras salir del bucle, liberando antes el `SingleInstanceChecker`.
- `notificador/ntfy/`: protocolo, sin dependencias de wx.
  - `models.py`: `Message`, `Action`, `Attachment` y `PublishRequest`.
  - `publisher.py`: construcción y envío de publicaciones, límites de adjuntos (`/v1/account`, con los límites documentados como respaldo) y validación.
  - `subscriber.py`: `SubscriptionManager`. Un hilo con un bucle asyncio y una conexión por servidor que agrupa sus temas. Usa WebSocket y, si no está disponible, el flujo `/json`. Reconecta con `since`.
  - `actions.py`: ejecución de acciones `view`, `http` y `copy`.
- `notificador/storage.py`: SQLite con un `RLock`. Se usa desde varios hilos.
- `notificador/vault.py`: credenciales por servidor, cifradas con AES-GCM y clave derivada con scrypt a partir de la contraseña maestra.
- `notificador/config.py`: `data/config.json`. Usa `normalize_server()` para cualquier URL de servidor.
- `notificador/toasts.py`: toasts de WinRT (`windows-toasts`), registro del AUMID en HKCU y correspondencia entre prioridades y comportamiento del toast. Si WinRT no está disponible, recurre a `wx.adv.NotificationMessage`.
- `notificador/i18n.py`: `install()`, `resolve()`, `available_languages()`, `create_wx_locale()`.
- `notificador/formatting.py`: textos de la lista, del panel de contenido y de los toasts (emojis de las etiquetas).
- `notificador/ui/`: ventana principal, diálogos, bandeja y `a11y.py` (helpers de formularios accesibles).

## Convenciones

- Indentación con **tabuladores**. Comentarios, docstrings y textos de la interfaz en **español**.
- Toda cadena visible pasa por `_()` (o `ngettext`), importados de `notificador.i18n`. No uses `_` como nombre de variable descartable; usa `_x` o similar.
- **Finales de línea:** el repositorio mezcla LF y CRLF a propósito. Respeta el formato que ya tenga cada archivo y no normalices. Al editar con scripts de Python en Windows, abre los archivos con `newline=""` para no convertir LF en CRLF.

## Accesibilidad (obligatorio en la interfaz)

- Usa solo controles nativos de wx; nada dibujado a mano.
- Cada control va precedido de su `wx.StaticText`, creado justo antes, porque así lo toman los lectores como nombre. Además se le asigna `SetName()`. Para formularios usa `ui/a11y.Form`, que ya lo hace.
- Mnemónicos `&` en etiquetas y botones, sin repetir letras dentro del mismo diálogo. El orden de tabulación es el orden de creación.
- **Foco inicial:** al abrirse un diálogo, el foco debe quedar en su primer campo. Lo normal es no llamar a `SetFocus()` en el constructor y dejar que wx enfoque el primer control. Excepción actual: `PublishDialog` va al mensaje cuando recibe un tema.
- Los errores de validación se muestran con `a11y.error(parent, mensaje, control)`, que devuelve el foco al campo erróneo.
- No reescribas el contenido de un control que el usuario pueda estar leyendo. Por ejemplo, la ventana principal no refresca el cuadro de contenido si la notificación seleccionada no cambia.
- Los estados importantes se comunican con texto, no solo con formato visual (por ejemplo, la columna «Estado: No leída»).

## Hilos

- Los callbacks del `SubscriptionManager`, de los hilos de publicación y de los toasts (`on_activated`) no se ejecutan en el hilo de wx. Toda interacción con la interfaz pasa por `wx.CallAfter`.
- `Storage` sí se puede usar desde cualquier hilo.
- No bloquees el bucle asyncio del suscriptor. Para descargas usa `Controller.executor`.

## Portabilidad y datos

- Todo se guarda en `paths.data_dir()` (`data/` junto al `.exe` o al código): configuración, `credentials.vault`, la base de datos, adjuntos, iconos y el log. No escribas en otras rutas.
- La única huella fuera de la carpeta es la clave `HKCU\Software\Classes\AppUserModelId\<AUMID>`. Si se registran AUMID de prueba desde copias temporales, hay que eliminarlos con `toasts.unregister_aumid()`.
- En las pruebas, `tests/conftest.py` redirige `data_dir` a un directorio temporal mediante `paths.set_data_dir()`.

## Protocolo ntfy

- Documentación de referencia: https://docs.ntfy.sh/publish/ y https://docs.ntfy.sh/subscribe/api/. No añadas parámetros que no estén documentados allí.
- La publicación sin archivo va por POST JSON a la raíz del servidor. Con archivo local va por PUT al tema, con los parámetros en la query string (admite UTF-8). Con plantilla va por POST al tema.
- El parámetro `?auth=` es la cabecera `Authorization` en base64 URL-safe sin relleno.
- Límites documentados: mensaje de 4096 bytes, título de 1 KB, etiquetas de 512 bytes, 3 acciones. Adjuntos: 15 MB y 100 MB por defecto, 2 MB y 20 MB en ntfy.sh.
- `message_delete` y `message_clear` actúan sobre el `sequence_id`. Si no lo hay, se usa el `id` (`Message.effective_sequence_id`).

## Pruebas

- `pytest` cubre la lógica que no depende de wx. Los cambios en la interfaz se verifican además con una prueba de humo: crear la `wx.App` y el `Controller` con un `data_dir` temporal, construir los diálogos y destruirlos.
- Para probar contra ntfy.sh real, usa temas aleatorios (`notificador_test_<hex>`).

## Git

- Los mensajes de commit van en español: un título breve en imperativo y, si hace falta, una lista de cambios.
- Se trabaja directamente en `master` y no hay remoto.
- Haz commit solo cuando se pida.
