"""La interfaz se prueba de verdad: se crea la ventana, se convierte y se guarda."""

import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app(pdf):
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("sin pantalla (usa xvfb-run)")
    from papiro.gui import App

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no se puede abrir una ventana: {exc}")
    root.withdraw()
    a = App(root, [pdf("03_tabla.pdf"), pdf("07_tategaki_furigana.pdf")])
    yield a
    root.destroy()


def pump(app, until, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        app.root.update()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_convertir_sin_congelar_y_guardar(app, tmp_path, pdf):
    from papiro.gui import DONE, MODES

    assert len(app.items) == 2
    assert app.add_paths([pdf("03_tabla.pdf")]) == 0  # no se duplica
    app.mode.set(next(k for k, v in MODES.items() if v == "rapido"))
    app.start()
    assert str(app.convert_btn["state"]) == "disabled"
    assert pump(app, lambda: not app._busy() and str(app.convert_btn["state"]) == "normal")
    assert [it["status"] for it in app.items.values()] == [DONE, DONE]
    assert app.progress["value"] >= 1000
    assert "| Mes | Ingresos | Gastos | Saldo |" in app.text.get("1.0", "end")

    # la vista previa es editable y lo editado es lo que se guarda
    app.text.insert("1.0", "EDITADO\n")
    second = app.tree.get_children()[1]
    app.tree.selection_set(second)
    app.root.update()
    assert app.text.get("1.0", "end").startswith("# 山(やま)の小(ちい)さな駅(えき)")
    assert app.save_all_to(str(tmp_path)) == 2
    assert (tmp_path / "03_tabla.md").read_text(encoding="utf-8").startswith("EDITADO")
    assert "朝(あさ)の電車(でんしゃ)" in (tmp_path / "07_tategaki_furigana.md").read_text(encoding="utf-8")

    app.copy()
    assert app.root.clipboard_get().startswith("# 山")


def test_error_y_furigana(app, tmp_path):
    from papiro.gui import FAIL, MODES

    bad = tmp_path / "roto.pdf"
    bad.write_bytes(b"no soy un pdf")
    app.clear()
    app.add_paths([str(bad)])
    app.mode.set(next(k for k, v in MODES.items() if v == "rapido"))
    app.furigana.set(False)
    app.start()
    assert pump(app, lambda: not app._busy() and str(app.convert_btn["state"]) == "normal")
    item = next(iter(app.items.values()))
    assert item["status"] == FAIL and "No se pudo abrir el PDF" in item["warnings"][0]
    assert "No se pudo convertir" in app.notes.get("1.0", "end")
