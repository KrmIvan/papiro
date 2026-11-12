import subprocess
import sys

from papiro.cli import main


def run(*args, stdin: bytes | None = None):
    return subprocess.run([sys.executable, "-m", "papiro", *args], input=stdin, capture_output=True)


def test_un_archivo_a_stdout(pdf):
    r = run(pdf("03_tabla.pdf"), "--modo", "rapido")
    assert r.returncode == 0
    assert "| Mes | Ingresos | Gastos | Saldo |" in r.stdout.decode("utf-8")


def test_salida_a_archivo(pdf, tmp_path):
    out = tmp_path / "sub" / "salida.md"
    assert main([pdf("07_tategaki_furigana.pdf"), "-o", str(out), "--modo", "rapido", "-q"]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 山(やま)の小(ちい)さな駅(えき)")
    assert main([pdf("07_tategaki_furigana.pdf"), "-o", str(out), "--modo", "rapido", "-q", "--sin-furigana"]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 山の小さな駅")


def test_varios_archivos_y_carpeta(pdf, tmp_path):
    src = tmp_path / "docs"
    (src / "dentro").mkdir(parents=True)
    (src / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (src / "dentro" / "b.json").write_text('{"x": 1}', encoding="utf-8")
    (src / "ignorar.bin").write_bytes(b"\x00")
    out = tmp_path / "md"
    assert main([str(src), pdf("03_tabla.pdf"), "-o", str(out), "--modo", "rapido", "-q"]) == 0
    assert (out / "a.md").exists() and (out / "dentro" / "b.md").exists() and (out / "03_tabla.md").exists()
    assert not (out / "ignorar.md").exists()
    # sin -o, cada .md va junto a su original
    assert main([str(src), "--modo", "rapido", "-q"]) == 0
    assert (src / "a.md").exists() and (src / "dentro" / "b.md").exists()


def test_stdin(pdf):
    data = open(pdf("08_vocabulario.pdf"), "rb").read()
    r = run("-", "--modo", "rapido", stdin=data)
    assert r.returncode == 0 and "| しゅうまつ | 週末 | fin de semana |" in r.stdout.decode("utf-8")
    r = run("-", "--formato", "csv", stdin="x;y\n猫;gato\n".encode("utf-8"))
    assert "| 猫 | gato |" in r.stdout.decode("utf-8")


def test_error_claro_y_codigo_de_salida(tmp_path):
    r = run(str(tmp_path / "no_existe.pdf"))
    assert r.returncode == 1 and "ERROR" in r.stderr.decode("utf-8")
    bad = tmp_path / "roto.pdf"
    bad.write_bytes(b"esto no es un pdf")
    r = run(str(bad))
    assert r.returncode == 1 and "No se pudo abrir el PDF" in r.stderr.decode("utf-8")


def test_estado_y_version():
    assert "papiro" in run("--version").stdout.decode()
    assert "OCR activo" in run("--estado").stdout.decode("utf-8")
