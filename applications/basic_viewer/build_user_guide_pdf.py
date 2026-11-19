"""Render USER_GUIDE.md to an A4 PDF for the Windows package (run with the Linux viewer Python, PySide6)."""
import argparse
import hashlib
import os
from pathlib import Path
import sys

from PySide6.QtCore import QMarginsF, QSizeF, QUrl
from PySide6.QtGui import (QFont, QGuiApplication, QImage, QPageLayout, QPageSize, QPdfWriter,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
                           QTextFrameFormat, QTextLength, QTextTable, QTextTableFormat, QColor)

ROOT = Path(__file__).resolve().parent
HEADING_POINTS = {1: 18, 2: 14, 3: 12}


def style_document(document):
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        level = block.blockFormat().headingLevel()
        if level:
            fmt = QTextBlockFormat(block.blockFormat())
            fmt.setTopMargin(14 if level <= 2 else 10)
            fmt.setBottomMargin(6)
            cursor.setPosition(block.position())
            cursor.setPosition(block.position() + block.length() - 1, QTextCursor.KeepAnchor)
            cursor.setBlockFormat(fmt)
            char = QTextCharFormat()
            char.setFontPointSize(HEADING_POINTS.get(level, 11))
            char.setForeground(QColor("#1f3b5c"))
            cursor.mergeCharFormat(char)
        block = block.next()
    for frame in document.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            fmt = QTextTableFormat(frame.format())
            fmt.setBorder(1)
            fmt.setBorderBrush(QColor("#8a99ab"))
            fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
            fmt.setCellPadding(4)
            fmt.setCellSpacing(0)
            fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
            frame.setFormat(fmt)
            for row in range(frame.rows()):
                if row and row % 2:
                    continue
                for column in range(frame.columns()):
                    cell = frame.cellAt(row, column)
                    char = cell.format()
                    char.setBackground(QColor("#dfe7f1" if row == 0 else "#f3f6fa"))
                    cell.setFormat(char.toTableCellFormat())


def fit_images(document, base, max_width):
    """Load Markdown images relative to the guide and shrink them to the text width."""
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fmt = fragment.charFormat()
            if fmt.isImageFormat():
                image_format = fmt.toImageFormat()
                path = base / image_format.name()
                image = QImage(str(path))
                if image.isNull():
                    raise FileNotFoundError(f"USER_GUIDE image not found: {path}")
                document.addResource(QTextDocument.ImageResource, QUrl(image_format.name()), image)
                width = min(float(image.width()), max_width)
                image_format.setWidth(width)
                image_format.setHeight(image.height() * width / image.width())
                cursor = QTextCursor(document)
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                cursor.setCharFormat(image_format)
            iterator += 1
        block = block.next()


def build(source, target):
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    document = QTextDocument()
    font = QFont("DejaVu Sans")
    font.setPointSizeF(9.5)
    document.setDefaultFont(font)
    document.setMarkdown(text, QTextDocument.MarkdownDialectGitHub)
    style_document(document)
    writer = QPdfWriter(str(target))
    writer.setTitle("Comparison Viewer - User Guide")
    # Records which USER_GUIDE.md produced the PDF.
    writer.setCreator("USER_GUIDE.md sha256 " + hashlib.sha256(raw).hexdigest())
    writer.setResolution(300)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait,
                                     QMarginsF(16, 16, 16, 16), QPageLayout.Millimeter))
    # Lay out in CSS pixels (96 per inch); print_ scales the pages to the PDF resolution.
    page = writer.pageLayout().paintRectPoints().size()
    document.setPageSize(QSizeF(page.width() * 96 / 72, page.height() * 96 / 72))
    document.setDocumentMargin(0)
    fit_images(document, source.parent, document.pageSize().width())
    document.print_(writer)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--source", type=Path, default=ROOT / "USER_GUIDE.md")
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv[:1])
    build(args.source, args.output)
    del app


if __name__ == "__main__":
    main()
