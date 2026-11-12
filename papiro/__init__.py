"""Papiro: convierte documentos a Markdown limpio para IA, todo en local.

    from papiro import Converter
    texto = Converter().convert("libro.pdf").text_content
"""

from .converter import ConversionResult, Converter, UnsupportedFormat, iter_files

__version__ = "0.1.0"
__all__ = ["Converter", "ConversionResult", "UnsupportedFormat", "iter_files", "__version__"]
