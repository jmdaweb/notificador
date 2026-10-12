import requests

from notificador import net
from notificador.vault import Vault


def test_from_config_is_tolerant():
	assert net.ProxySettings.from_config(None) == net.ProxySettings()
	settings = net.ProxySettings.from_config({"type": "raro", "port": "x", "host": " h "})
	assert settings.type == "system" and settings.port == 0 and settings.host == "h"
	settings = net.ProxySettings.from_config({"type": "socks5", "host": "h", "port": "1081", "username": "u"}, "p")
	assert (settings.type, settings.port, settings.username, settings.password) == ("socks5", 1081, "u", "p")


def test_to_config_never_contains_password():
	data = net.ProxySettings("http", "h", 3128, "u", "secreta").to_config()
	assert "secreta" not in str(data) and data["has_password"] is True


def test_urls_and_default_ports():
	assert net.ProxySettings("http", "proxy.local").url() == "http://proxy.local:8080"
	assert net.ProxySettings("https", "proxy.local").url() == "https://proxy.local:443"
	assert net.ProxySettings("socks4", "10.0.0.1").url() == "socks4a://10.0.0.1:1080"
	assert net.ProxySettings("socks5", "10.0.0.1", 9050).url() == "socks5h://10.0.0.1:9050"
	assert net.ProxySettings("socks5", "::1", 1080).url() == "socks5h://[::1]:1080"


def test_credentials_are_escaped():
	url = net.ProxySettings("http", "h", 3128, "ana@empresa", "p:a/s s").url()
	assert url == "http://ana%40empresa:p%3Aa%2Fs%20s@h:3128"
	# Usuario sin contraseña: websockets exige ambos, así que se envía la contraseña vacía.
	assert net.ProxySettings("socks5", "h", 1080, "ana").url() == "socks5h://ana:@h:1080"


def test_display_hides_password():
	shown = net.ProxySettings("http", "h", 3128, "ana", "secreta").display()
	assert "secreta" not in shown and "ana@h:3128" in shown


def test_validation():
	assert net.ProxySettings("http", "").validate() == "missing_host"
	assert net.ProxySettings("http", "http://h").validate() == "missing_host"
	assert net.ProxySettings("http", "h", 70000).validate() == "invalid_port"
	assert net.ProxySettings("http", "h").validate() is None
	assert net.ProxySettings("system").validate() is None
	assert net.ProxySettings("http", "").url() is None


def test_requests_and_websocket_arguments():
	assert net.requests_proxies(net.ProxySettings("system")) is None
	assert net.requests_proxies(net.ProxySettings("none")) == {"http": None, "https": None, "all": None}
	manual = net.ProxySettings("socks5", "h", 1080)
	assert net.requests_proxies(manual) == {"http": "socks5h://h:1080", "https": "socks5h://h:1080"}
	assert net.websocket_proxy(net.ProxySettings("system")) is True
	assert net.websocket_proxy(net.ProxySettings("none")) is None
	assert net.websocket_proxy(manual) == "socks5h://h:1080"


def test_none_mode_overrides_environment(monkeypatch):
	monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
	monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")
	session = net.session(net.ProxySettings("none"))
	settings = session.merge_environment_settings(
		"https://ntfy.sh/", net.requests_proxies(net.ProxySettings("none")), None, None, None
	)
	assert requests.sessions.merge_setting(settings["proxies"], session.proxies) == {}


def test_session_injects_current_proxy(monkeypatch):
	captured = {}

	def fake_request(self, method, url, **kwargs):
		captured.update(kwargs)
		return "respuesta"

	monkeypatch.setattr(requests.Session, "request", fake_request)
	previous = net.current()
	try:
		net.configure(net.ProxySettings("http", "h", 3128))
		with net.use() as http:
			assert http.request("GET", "https://ntfy.sh") == "respuesta"
		assert captured["proxies"] == {"http": "http://h:3128", "https": "http://h:3128"}
		captured.clear()
		net.configure(net.ProxySettings("system"))
		net.session().request("GET", "https://ntfy.sh")
		assert "proxies" not in captured
	finally:
		net.configure(previous)


def test_use_keeps_given_session():
	marker = object()
	with net.use(marker) as http:
		assert http is marker


def test_vault_secrets_are_not_servers(data_dir):
	vault = Vault()
	vault.create("secreto1")
	vault.set("https://ntfy.sh", "token", "", "tk")
	vault.set_secret(net.VAULT_SECRET, "clave-proxy")
	assert vault.servers() == ["https://ntfy.sh"]
	locked = Vault()
	assert locked.get_secret(net.VAULT_SECRET) == ""
	locked.unlock("secreto1")
	assert locked.get_secret(net.VAULT_SECRET) == "clave-proxy"
	locked.delete_secret(net.VAULT_SECRET)
	assert locked.get_secret(net.VAULT_SECRET) == ""
