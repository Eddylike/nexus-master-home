from nexus.common.leitbild import LEITBILD, check_download_url, ai_label, requires_approval, send_allowed, mail_phishing_hints


def test_leitbild_text():
    assert "Mensch und Maschine" in LEITBILD and "kostenlos" in LEITBILD


def test_download_guard():
    assert check_download_url("https://example.org/film.mp4") == {"ok": True, "warnings": []}
    assert not check_download_url("file:///C:/x.exe")["ok"]
    w = check_download_url("http://192.168.1.5/setup.exe")["warnings"]
    assert any("IP" in x for x in w) and any(".exe" in x for x in w)
    assert check_download_url("https://bit.ly/abc")["warnings"]
    assert check_download_url("https://xn--pypal-4ve.com/")["warnings"]


def test_studio():
    m = ai_label({"w": 1}, generator="t2i")
    assert m["ai_generated"] and m["label"] == "KI-erzeugt" and m["w"] == 1
    assert requires_approval("delete") and requires_approval("Overwrite") and not requires_approval("preview")


def test_mail():
    assert not send_allowed({"to": "a@b.de"}) and not send_allowed(None) and send_allowed({"approved": True})
    h = mail_phishing_hints('"PayPal.com Service" <x@evil.ru>', "Konto gesperrt", "Bitte sofort http://1.2.3.4/login", ["rechnung.pdf.exe"])
    assert len(h) == 4
    assert mail_phishing_hints("Anna <anna@web.de>", "Treffen", "Bis morgen!") == []
