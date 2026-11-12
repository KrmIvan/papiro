from papiro.textutil import (
    broken_text_score, detect_language, join_lines, looks_broken, normalize_text, strip_cjk_spaces, strip_furigana,
)


def test_join_japones_sin_espacios():
    assert join_lines(["今日は天気が", "いいですね。"]) == "今日は天気がいいですね。"
    assert join_lines(["「こんにちは」と", "言った。"]) == "「こんにちは」と言った。"
    assert join_lines(["青(あお)い山(やま)", "が見(み)えた。"]) == "青(あお)い山(やま)が見(み)えた。"


def test_join_guiones_solo_en_latino():
    assert join_lines(["una con-", "versión limpia"]) == "una conversión limpia"
    assert join_lines(["el texto", "sigue aquí"]) == "el texto sigue aquí"
    assert join_lines(["precio: 10-", "20 euros"]) == "precio: 10- 20 euros"  # no es una palabra partida
    assert join_lines(["コーヒー", "を飲みます"]) == "コーヒーを飲みます"  # ー no es un guion
    assert join_lines(["significa", "食べます en japonés"]) == "significa 食べます en japonés"


def test_normalize_no_rompe_el_japones():
    assert normalize_text("ｶﾞｷﾞｸﾞ ﾊﾟﾝ") == "ガギグ パン"  # katakana de ancho medio
    assert normalize_text("が") == "が"  # dakuten suelto
    assert normalize_text("⽇本語") == "日本語"  # radical Kangxi en vez de kanji
    assert normalize_text("ﬁn de la ﬂor") == "fin de la flor"
    assert normalize_text("cañon") == "cañon"
    # la puntuación y los caracteres de ancho completo se respetan
    for s in ("「はい」、『いいえ』。", "１２３ＡＢＣ", "（かっこ）！？", "…・〜ー", "①②"):
        assert normalize_text(s) == s
    assert normalize_text("﹁はい﹂︒") == "「はい」。"  # formas verticales


def test_idioma():
    assert detect_language("わたしは毎朝六時に起きます。") == "ja"
    assert detect_language("La reunión comenzó a las siete de la tarde en el salón.") == "es"
    assert detect_language("The meeting started at seven in the evening and it was long.") == "en"
    assert detect_language("La partícula を marca el objeto: パンを食べます significa «como pan».") == "mixto"
    assert detect_language("12") == "desconocido"


def test_texto_ilegible():
    assert not looks_broken("La conversión de documentos parece sencilla. ¿Verdad? ¡Sí!")
    assert not looks_broken("朝の電車は、霧の中をゆっくり走っていた。窓の外には田んぼが広がり、遠くに青い山が見えた。")
    assert looks_broken("`\x14\x89VBí\x07®Ö\x05Åýã\x08åÃ/HFFLµQ\x16 3ODQHVSDUDHOILQ")
    assert looks_broken("(cid:12)(cid:45)(cid:7)(cid:88)(cid:19)(cid:3)")
    assert looks_broken("��� abc ����")
    assert looks_broken("æ—¥æœ¬èªžã®ãƒ†ã‚­ã‚¹ãƒˆã§ã™ã€‚ã“ã‚Œã¯æ–‡å­—åŒ–ã‘")  # UTF-8 leído como Latin-1
    assert broken_text_score("") == 0.0


def test_furigana_y_espacios():
    assert strip_furigana("漢字(かんじ)を読(よ)みます (nota)") == "漢字を読みます (nota)"
    assert strip_cjk_spaces("朝 の 電車 は 、 霧") == "朝の電車は、霧"
