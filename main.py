import csv
import re
import sqlite3
import sys
import webbrowser
from datetime import datetime
from functools import partial
from os import remove, system, replace, devnull
import subprocess

from PIL import Image
from PyQt5 import QtGui
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QIcon, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QColorDialog, QDialog, QErrorMessage,
                             QFileDialog, QFontDialog, QMainWindow, QMenu, QMessageBox,
                             QTableWidgetItem, QToolBar)
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFPageCountError

from pyqt5_code import AddThemeDialog, EditorMainWindow, SliderDialog, SnippetsSettingsDialog

# todo: Проверить

divider_ratio = .5  # [0.25; 0.75]
con = sqlite3.connect("DATA/editor_db.sqlite")
cur = con.cursor()


def get_colors_from_db():
    global con, cur
    themes = cur.execute("""SELECT * FROM themes""")
    colors = {}
    for theme in themes:
        colors[str(theme[0])] = {"background": list(map(int, theme[1].split(", "))),
                                 "text_color": list(map(int, theme[2].split(", "))),
                                 "plain_text": list(map(int, theme[3].split(", "))),
                                 "variables": list(map(int, theme[4].split(", ")))}
    return colors


COLORS = get_colors_from_db()

BEGIN_TEMPLATE = """\\documentclass[12pt]{article}
\\usepackage{amsmath}
\\usepackage{amssymb}
\\usepackage{amsfonts}
\\usepackage{xcolor}
\\usepackage{siunitx}
\\usepackage[utf8]{inputenc}
\\thispagestyle{empty}
\\begin{document}\n"""
END_TEMPLATE = """\n\\end{document}"""


def crop_image(image_path, text_color):
    # Вырезает текст из картинки
    img = Image.open(image_path)

    pxls = img.load()
    width, height = img.size[0], img.size[1]
    min_x, min_y, max_x, max_y = -1, -1, -1, -1

    for x in range(width):
        for y in range(height):
            if pxls[x, y] == text_color:
                if x < min_x or min_x == -1:
                    min_x = x
                elif x > max_x:
                    max_x = x

                if y < min_y or min_y == -1:
                    min_y = y
                elif y > max_y:
                    max_y = y

    img = img.crop((min_x - 10, min_y - 10, max_x + 10, max_y + 10))
    img.save(image_path)


def latex_to_png(filename):
    # Перевод кода latex в png
    # system("pdflatex Latex/preview.tex -halt-on-error")
    subprocess.call(["pdflatex", "Latex/preview.tex", "-halt-on-error"],
                    stdout=open(devnull, "w"), stderr=subprocess.STDOUT)
    remove(f"preview.aux")
    remove(f"preview.log")
    images = convert_from_path("preview.pdf", single_file=True)
    remove(f"preview.pdf")
    images[0].save(filename)
    crop_image(filename, (0, 0, 0))


def get_color(color):
    return ", ".join([str(elem) for elem in color])


def resize_image(label_width, label_height):
    img = Image.open("IMG/preview.png")
    width, height = img.size[0], img.size[1]
    width_difference = label_width - width
    height_difference = label_height - height
    if width_difference > 0 and height_difference > 0:
        return
    elif width_difference <= height_difference:
        img.thumbnail(size=(label_width, height))
    else:
        img.thumbnail(size=(width, label_height))
    img.save("IMG/preview.png")


class LatexEditor(QMainWindow, EditorMainWindow):
    # Главный класс редактора
    def __init__(self):
        global con, cur
        super().__init__()
        self.setupUi(self)
        self.setWindowIcon(QIcon("IMG/latex_logo.png"))
        self.setMinimumSize(960, 960)
        self.filename = None
        self.plain_text.textChanged.connect(self.text_changed)
        self.opening_brackets = 0
        self.recursion = 0
        self.file_saved = True
        self.previous_len = 0
        settings = cur.execute("""SELECT * FROM settings""").fetchall()[0]
        self.font_size, self.font_family, self.theme = settings[0], settings[1], settings[2]
        self.plain_text.setFont(QFont(self.font_family, self.font_size))
        self.theme -= 1
        self.error_dialog = QErrorMessage(self)
        self.image.setAlignment(Qt.AlignTop)

        # Создание панели инструментов
        self.toolbar = QToolBar("Toolbar")
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)
        self.toolbar.setMovable(False)

        # Создание панели меню
        self.menubar = self.menuBar()
        self.file_menu = self.menubar.addMenu("File")
        self.edit_menu = self.menubar.addMenu("Edit")
        self.tools_menu = self.menubar.addMenu("Tools")
        self.recent_menu = self.file_menu.addMenu("Открыть недавнее")
        self.context_menu = QMenu(self.plain_text)

        # Создание действий для self.toolbar
        self.open_file_action = QAction(QIcon("IMG/open_file.png"), "Открыть файл", self)
        self.open_file_action.setShortcut("Ctrl+O")
        self.open_file_action.triggered.connect(self.open_file_dialog)

        self.save_file_action = QAction(QIcon("IMG/save_file.png"), "Сохранить файл", self)
        self.save_file_action.setShortcut("Ctrl+S")
        self.save_file_action.triggered.connect(self.save_file)

        self.save_pdf_action = QAction(QIcon("IMG/pdf.png"), "Сохранить PDF", self)
        self.save_pdf_action.setShortcut("Ctrl+Alt+S")
        self.save_pdf_action.triggered.connect(self.save_pdf)

        self.save_png_action = QAction(QIcon("IMG/image.png"), "Сохранить PNG", self)
        self.save_png_action.setShortcut("Ctrl+Shift+S")
        self.save_png_action.triggered.connect(self.save_png)

        self.add_template_action = QAction(QIcon("IMG/template.png"), "Добавить преамбулу", self)
        self.add_template_action.setShortcut("Ctrl+T")
        self.add_template_action.triggered.connect(self.add_template)

        self.change_divider_ratio_action = QAction(QIcon("IMG/divider.png"), "Разделитель", self)
        self.change_divider_ratio_action.setShortcut("Ctrl+D")
        self.change_divider_ratio_action.triggered.connect(self.open_slider_window)

        self.create_file_action = QAction(QIcon("IMG/create_file.png"), "Создать файл", self)
        self.create_file_action.setShortcut("Ctrl+N")
        self.create_file_action.triggered.connect(self.create_file)

        self.close_file_action = QAction(QIcon("IMG/close_file.png"), "Закрыть файл", self)
        self.close_file_action.setShortcut("Ctrl+Shift+X")
        self.close_file_action.triggered.connect(self.close_file)

        self.change_theme_action = QAction(QIcon("IMG/THEMES/1.png"), "Изменить тему", self)
        self.change_theme_action.setShortcut("Ctrl+Shift+D")
        self.change_theme_action.triggered.connect(self.change_theme)

        self.create_theme_action = QAction(QIcon("IMG/+.png"), "Добавить тему", self)
        self.create_theme_action.setShortcut("Ctrl+Shift+T")
        self.create_theme_action.triggered.connect(self.create_theme)

        self.delete_theme_action = QAction(QIcon("IMG/-.png"), "Добавить тему", self)
        self.delete_theme_action.setShortcut("Ctrl+Shift+T")
        self.delete_theme_action.triggered.connect(self.delete_theme)

        self.snippets_settings_action = QAction(QIcon("IMG/settings.png"), "Настроить сниппеты",
                                                self)
        self.snippets_settings_action.setShortcut("Ctrl+Shift+Alt+S")
        self.snippets_settings_action.triggered.connect(self.snippets_settings)

        self.change_font_action = QAction(QIcon("IMG/choose_font.png"), "Выбрать шрифт", self)
        self.change_font_action.setShortcut("Ctrl+F")
        self.change_font_action.triggered.connect(self.change_font)

        self.increase_font_action = QAction(QIcon("IMG/increase_font_size.png"), "Увеличить шрифт"
                                                                                 "size", self)
        self.increase_font_action.setShortcut("Ctrl+1")
        self.increase_font_action.triggered.connect(self.increase_font)

        self.decrease_font_action = QAction(QIcon("IMG/decrease_font_size.png"), "Уменьшить шрифт"
                                                                                 "size", self)
        self.decrease_font_action.setShortcut("Ctrl+0")
        self.decrease_font_action.triggered.connect(self.decrease_font)

        self.open_latex_guide_action = QAction(QIcon("IMG/latex_guide.png"), "Открыть справочник",
                                               self)
        self.open_latex_guide_action.setShortcut("Ctrl+G")
        self.open_latex_guide_action.triggered.connect(self.open_latex_guide)
        self.separator = QAction(self)
        self.separator.isSeparator()

        # Подключение действий к self.toolbar
        self.toolbar.addAction(self.open_file_action)
        self.toolbar.addAction(self.save_file_action)
        self.toolbar.addAction(self.save_pdf_action)
        self.toolbar.addAction(self.save_png_action)
        self.toolbar.addAction(self.add_template_action)
        self.toolbar.addAction(self.create_file_action)
        self.toolbar.addAction(self.close_file_action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.change_divider_ratio_action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.change_theme_action)
        self.toolbar.addAction(self.create_theme_action)
        self.toolbar.addAction(self.delete_theme_action)
        self.toolbar.addAction(self.snippets_settings_action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.change_font_action)
        self.toolbar.addAction(self.increase_font_action)
        self.toolbar.addAction(self.decrease_font_action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.open_latex_guide_action)

        # Подключение действий к self.menubar
        self.file_menu.addAction(self.open_file_action)
        self.file_menu.addAction(self.save_file_action)
        self.file_menu.addAction(self.save_pdf_action)
        self.file_menu.addAction(self.save_png_action)
        self.file_menu.addAction(self.close_file_action)
        self.file_menu.addAction(self.open_latex_guide_action)
        self.recent_files_to_menu()
        self.edit_menu.addAction(self.change_font_action)
        self.edit_menu.addAction(self.increase_font_action)
        self.edit_menu.addAction(self.decrease_font_action)
        self.edit_menu.addAction(self.change_theme_action)
        self.edit_menu.addAction(self.create_theme_action)
        self.edit_menu.addAction(self.delete_theme_action)
        self.tools_menu.addAction(self.change_divider_ratio_action)
        self.tools_menu.addAction(self.add_template_action)
        self.change_theme()

        # Подключение действий к контекстному меню
        self.context_menu.addAction(self.save_file_action)
        self.context_menu.addAction(self.open_file_action)
        self.context_menu.addAction(self.open_file_action)
        self.context_menu.addAction(self.separator)
        self.context_menu.addAction(self.add_template_action)
        self.context_menu.addAction(self.open_latex_guide_action)

        # Если приложение запущено впервые, будет показано окно с просьбой прочитать файл
        # README.md. Для того, чтобы понять, запущено ли приложение впервые, используется файл
        # DATA/first_app.txt.txt, при запуске программа пытается его удалить. Если получилось,
        # значит, файл существует, т.е. приложение запущено впервые, показывается окно. Если же
        # файла нет, он уже удалён, приложение запущено не впервые, ничего не происходит.
        try:
            remove("DATA/first_app.txt.txt")
            QMessageBox.information(self, "README",
                                    "Перед использованием приложения, пожалуйста, "
                                    "прочитайте файл README.md и выполните "
                                    "инструкции, указанные в нём.")
        except FileNotFoundError:
            ...

    def snippets_settings(self):
        # Открывает окно настройки сниппетов
        snippets_settings_window = SnippetsWindow(self)
        snippets_settings_window.show()

    def recent_files_to_menu(self):
        # Добавляет недавно открытые файлы в соответствующее меню
        with open("DATA/recent_files.csv", mode="r", encoding="utf-8") as csv_file:
            data = csv.DictReader(csv_file, delimiter=",", quotechar="'")
            data = sorted(data, key=lambda line: datetime.strptime(
                line["time"], "%Y/%m/%d %H/%M/%S"), reverse=True)[:5]
        if self.filename and self.filename not in [elem["filename"] for elem in data]:
            # Положить
            data.insert(0, {"time": datetime.strftime(datetime.now(), "%Y/%m/%d %H/%M/%S"),
                            "filename": self.filename})
            data = data[:5]
            with open("DATA/recent_files.csv", mode="w", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(csv_file, delimiter=",", quotechar="'",
                                        quoting=csv.QUOTE_ALL, fieldnames=["time", "filename"])
                writer.writeheader()
                for elem in data:
                    writer.writerow(elem)
        # Добавление в меню
        self.recent_menu.clear()
        actions = []
        for index, filename in enumerate([elem["filename"] for elem in data]):
            action = QAction(QIcon(f"IMG/RECENT/{index + 1}.png"),
                             filename.rsplit("/", maxsplit=1)[-1], self)
            action.triggered.connect(partial(self.open_file, filename))
            actions.append(action)
        self.recent_menu.addActions(actions)

    def open_file(self, filename):
        # Открывает файл по названию
        with open(filename, mode="r", encoding="utf-8") as latex_file:
            text = latex_file.read()
        self.plain_text.setPlainText(text)
        self.filename = filename
        self.file_saved = False
        self.save_file()

    def create_theme(self):
        # Создаёт тему и сохраняет её в БД
        if len(COLORS) == 9:
            self.error_dialog.showMessage("Достигнут лимит сохранения тем (9)")
            return
        add_theme_window = AddThemeWindow(len(COLORS) + 1, self)
        add_theme_window.show()

    def delete_theme(self):
        # Удаляет текущую тему из БД
        global con, cur, COLORS
        cur.execute(f"DELETE from themes WHERE number = {self.theme}")
        con.commit()
        COLORS = get_colors_from_db()
        if self.theme > len(COLORS):
            self.theme -= 1
        self.change_theme()

    def increase_font(self):
        # Увеличивает шрифт
        self.font_size += 1
        self.plain_text.setFont(QFont(self.font_family, self.font_size))

    def decrease_font(self):
        # Уменьшает шрифт
        self.font_size -= 1
        self.plain_text.setFont(QFont(self.font_family, self.font_size))

    def save_png(self):
        # Сохранение файла как PNG
        self.save_file()
        filename, ok_pressed = QFileDialog.getSaveFileName(self, "Сохранить файл", "",
                                                           "PNG (*.png)")
        if ok_pressed:
            latex_to_png(filename)

    def open_latex_guide(self):
        # Открывает интернет-страницу со справочником по символам Latex
        webbrowser.open("https://oeis.org/wiki/List_of_LaTeX_mathematical_symbols")

    def change_font(self):
        # Изменение шрифта у QPlainText
        font, ok_pressed = QFontDialog.getFont()
        if ok_pressed:
            self.font_size = font.pointSize()
            self.font_family = font.family()
            self.plain_text.setFont(font)

    def add_template(self):
        # Добавление встроенной преамбулы документа
        text = self.plain_text.toPlainText()
        self.plain_text.setPlainText(BEGIN_TEMPLATE + text + END_TEMPLATE)

    def save_pdf(self):
        # Сохранение файла как PDF
        if not self.filename:
            self.error_dialog.showMessage("Вы ещё не сохранили файл Latex")
            return
        text = self.plain_text.toPlainText()
        if "\\documentclass" not in text or "\\begin{document}" not in text:
            template_dialog = QMessageBox.question(
                self, "Шаблон", "В вашем файле отсутствует преамбула, хотите использовать "
                                "встроенную? При отсутствии преамбулы невозможно сохранение как "
                                "PDF", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if template_dialog == QMessageBox.Yes:
                self.add_template()
            elif template_dialog == QMessageBox.No:
                self.error_dialog.showMessage("В файле отсутствует преамбула для сохранения")
                return
            else:
                return
        pdf_filename, ok_pressed = QFileDialog.getSaveFileName(self, "Сохранить файл", "",
                                                               "PDF (*.pdf)")
        self.save_file()
        if ok_pressed:
            try:
                # pdf, log, cp = PDFLaTeX.from_texfile(self.filename).create_pdf()
                system(f"pdflatex {self.filename} -halt-on-error")
                remove(f"{self.filename[:-4].split('/')[-1]}.aux")
                remove(f"{self.filename[:-4].split('/')[-1]}.log")
                replace(f"{self.filename[:-4].split('/')[-1]}.pdf", pdf_filename)
            except FileNotFoundError:
                self.error_dialog.showMessage("Ошибка в коде Latex")

    def open_file_dialog(self):
        # Открыть файл
        if not self.file_saved:
            save_dialog = QMessageBox.question(self, "Сохранение", "Хотите сохранить текущий файл?",
                                               QMessageBox.Yes | QMessageBox.No |
                                               QMessageBox.Cancel)
            if save_dialog == QMessageBox.Yes:
                self.save_file()
            elif save_dialog == QMessageBox.Cancel:
                return
        self.plain_text.setPlainText("")
        filename = QFileDialog.getOpenFileName(
            self, "Выбрать Latex-файл", "", "Latex (*.tex);;Все файлы (*)")[0]
        if not filename:
            return
        self.open_file(filename)

    def save_file(self):
        # Сохранить файл
        if not self.filename:
            self.filename, ok_pressed = QFileDialog.getSaveFileName(self, "Сохранить файл", "",
                                                                    "Latex-файлы (*.tex)")
            if not ok_pressed:
                return
        text = self.plain_text.toPlainText()
        with open(self.filename, mode="w", encoding="utf-8") as latex_file:
            latex_file.write(text)
        if "\\documentclass" not in text or "\\begin{document}" not in text:
            text = BEGIN_TEMPLATE + text + END_TEMPLATE
        with open("LATEX/preview.tex", mode="w", encoding="utf-8") as preview_file:
            preview_file.write(text)
        self.plain_text_to_preview()
        self.file_saved = True
        self.recent_files_to_menu()

    def resizeEvent(self, event: QtGui.QResizeEvent):
        # Выполняется при изменении размера окна
        # Подстраивает размеры области кода и превью под размер окна
        global divider_ratio
        super().resizeEvent(event)
        width, height = self.width(), self.height()
        if width > 1500:
            self.plain_text.resize(int((width - 30) * divider_ratio), height - 90)
            self.plain_text.move(10, 10)
            self.image.resize(width - int((width - 30) * divider_ratio) - 30, height - 90)
            self.image.move(int((width - 30) * divider_ratio) + 20, 10)
        else:
            self.plain_text.resize(width - 20, int((height - 70) * divider_ratio))
            self.plain_text.move(10, 10)
            self.image.resize(width - 20, height - int((height - 70) * divider_ratio) - 30)
            self.image.move(10, int((height - 70) * divider_ratio) + 20)

    def closeEvent(self, event: QtGui.QCloseEvent):
        # Открывает диалог с предложением сохранить текущий файл, если пользователь этого ещё не
        # сделал
        global con, cur
        cur.execute("""DELETE FROM settings""")
        cur.execute(f"""INSERT INTO settings (font_size,font_family,theme) 
        VALUES ({self.font_size}, '{self.font_family}', {self.theme})""")
        con.commit()
        if not self.file_saved:
            save_dialog = QMessageBox.question(self, "Сохранение", "Хотите сохранить текущий файл?",
                                               QMessageBox.Yes | QMessageBox.No |
                                               QMessageBox.Cancel)
            if save_dialog == QMessageBox.Yes:
                self.save_file()
                event.accept()
            elif save_dialog == QMessageBox.No:
                event.accept()
            else:
                event.ignore()
        try:
            remove("LATEX/preview.tex")
        except FileNotFoundError:
            ...

    def open_slider_window(self):
        # Открыть диалог с слайдером для выбора значения
        slider_window = SliderWindow(self)
        slider_window.show()

    def close_file(self):
        # Закрыть файл
        if not self.filename:
            return
        if not self.file_saved:
            save_dialog = QMessageBox.question(self, "Сохранение", "Хотите сохранить текущий файл?",
                                               QMessageBox.Yes | QMessageBox.No |
                                               QMessageBox.Cancel)
            if save_dialog == QMessageBox.Yes:
                self.save_file()
            elif save_dialog == QMessageBox.Cancel:
                return
        self.plain_text.setPlainText("")
        self.filename = None
        self.file_saved = True

    def create_file(self):
        # Создать файл
        self.filename, _ = QFileDialog.getSaveFileName(self, "Save file", "",
                                                       "Latex-файлы (*.tex)")

    def change_theme(self):
        # Позволяет переключаться между встроенными темами
        if f"{(self.theme + 1) % 9}" not in COLORS:
            self.theme = 1
        else:
            self.theme = (self.theme + 1) % 9
        self.change_theme_action.setIcon(QIcon(f"IMG/THEMES/{self.theme}.png"))
        style = f"background-color: rgb({get_color(COLORS[f'{self.theme}']['background'])}); " \
                f"color: rgb({get_color(COLORS[f'{self.theme}']['text_color'])});\n"
        self.plain_text.setStyleSheet(f"background: "
                                      f"rgb({get_color(COLORS[f'{self.theme}']['plain_text'])});")
        self.image.setStyleSheet(f"background: #fff;")
        self.setStyleSheet(style)
        self.menubar.setStyleSheet("background: #fff; color: #000;")
        self.add_variable_highlight()

    def add_space_before_back_slash(self):
        # Добавляет пробелы перед \ в Latex (стилизация)
        text = self.plain_text.toPlainText()
        back_slashes = set(re.findall(r"[^\s\\{$]+\\", text))
        if back_slashes:
            right_back_slashes = {back_slash[:-1] + " " + "\\" for back_slash in back_slashes}
            for back_slash, right_back_slash in zip(back_slashes, right_back_slashes):
                text = text.replace(back_slash, right_back_slash)
            cursor = self.plain_text.textCursor()
            position = cursor.position()
            self.plain_text.setPlainText(text)
            cursor.setPosition(position + 1)
            self.plain_text.setTextCursor(cursor)

    def add_closing_bracket(self):
        # Добавляет закрывающую скобку после открывающей (стилизация)
        text = self.plain_text.toPlainText()
        cursor = self.plain_text.textCursor()
        self.recursion = True
        position = cursor.position()
        if self.opening_brackets < text.count("{") and \
                (position == len(text) or text[position:].count("}") - text[position:].count("{")
                 >= 1) and abs(len(text) - self.previous_len) == 1:
            cursor.insertText("}")
            position = cursor.position()
            cursor.setPosition(position - 1)
        text = self.plain_text.toPlainText()
        self.plain_text.setTextCursor(cursor)
        self.opening_brackets = text.count("{")
        self.previous_len = len(text)

    def add_variable_highlight(self):
        # Добавляет подсветку
        text = self.plain_text.toPlainText()
        cursor = self.plain_text.textCursor()
        position = cursor.position()
        self.recursion += 1
        self.plain_text.setPlainText("")
        text = text.replace("{\\", "{_\\")
        space_symbols = re.findall(r"[\s_]+", text)
        for i in range(len(space_symbols)):
            if space_symbols[i] == "_":
                space_symbols[i] = ""
        word_pattern = re.compile(r"\\[^\s{}$]+")
        text = text.replace("{_\\", "{ \\")
        words = text.split()
        if len(space_symbols) < len(words):
            space_symbols += [""]
        colors = []
        for word in words:
            if not word_pattern.findall(word):
                colors.append(QColor(*COLORS[f"{self.theme}"]["text_color"]))
            else:
                colors.append(QColor(*COLORS[f"{self.theme}"]["variables"]))
        if len(space_symbols) > len(words):
            self.recursion += 1
            self.plain_text.insertPlainText(space_symbols[0])
            space_symbols = space_symbols[1:]
        for word, color, space_symbol in zip(words, colors, space_symbols):
            color_format = self.plain_text.currentCharFormat()
            if word_pattern.findall(word):
                word_index = word.find(word_pattern.findall(word)[0])
                color_format.setForeground(QColor(*COLORS[f"{self.theme}"]["text_color"]))
                self.plain_text.setCurrentCharFormat(color_format)
                self.recursion += 1
                self.plain_text.insertPlainText(word[:word_index])
                color_format.setForeground(color)
                self.plain_text.setCurrentCharFormat(color_format)
                self.recursion += 1
                self.plain_text.insertPlainText(word_pattern.findall(word)[0])
                self.recursion += 1
                color_format.setForeground(QColor(*COLORS[f"{self.theme}"]["text_color"]))
                self.plain_text.setCurrentCharFormat(color_format)
                self.plain_text.insertPlainText(
                    word[(word_index + len(word_pattern.findall(word)[0])):] + space_symbol)
            else:
                self.recursion += 1
                self.plain_text.insertPlainText(word + space_symbol)
            self.recursion += 1
        cursor.setPosition(position)
        self.plain_text.setTextCursor(cursor)

    def plain_text_to_preview(self):
        # Обновляет превью latex кода
        try:
            latex_to_png("IMG/preview.png")
            resize_image(self.image.width(), self.image.height())
        except PDFPageCountError:
            self.error_dialog.showMessage("Ошибка в коде Latex")
        else:
            pixmap = QPixmap("IMG/preview.png")
            remove("IMG/preview.png")
            self.image.setPixmap(pixmap)

    def replace_snippets(self):
        # Сниппеты
        global cur
        text = self.plain_text.toPlainText()
        cursor = self.plain_text.textCursor()
        position = cursor.position()
        snippets = cur.execute("""SELECT * FROM snippets""")
        for snippet in snippets:
            text = text.replace(snippet[1].replace("NEXT", "\n").replace("TAB", "\t"),
                                snippet[2].replace("NEXT", "\n").replace("TAB", "\t"))
        self.recursion += 1
        self.plain_text.setPlainText(text)
        cursor.setPosition(position)
        self.plain_text.setTextCursor(cursor)

    def text_changed(self):
        # Запускается при изменении кода и реализует стилизацию кода и подсветку
        if self.recursion:
            self.recursion -= 1
            return
        self.replace_snippets()
        self.add_space_before_back_slash()
        self.add_variable_highlight()
        self.add_closing_bracket()
        self.recursion = 0
        self.file_saved = False


class SnippetsWindow(QDialog, SnippetsSettingsDialog):
    # Класс для окна настройки сниппетов
    def __init__(self, parent=None):
        self.parent = parent
        super().__init__(self.parent)
        self.setupUi(self)
        self.setWindowIcon(QIcon("IMG/latex_logo.png"))
        self.ok_button.clicked.connect(self.save_data_to_db)
        self.add_row_button.clicked.connect(self.add_row)
        self.delete_button.clicked.connect(self.delete_rows)
        self.data_saved_to_db = True
        self.table.itemChanged.connect(self.data_unsaved)
        data = cur.execute("""SELECT * FROM snippets""").fetchall()
        self.table.setRowCount(len(data))
        self.table.setColumnCount(len(data[0]))
        self.titles = [description[0] for description in cur.description]
        for i, elem in enumerate(data):
            for j, val in enumerate(elem):
                self.table.setItem(i, j, QTableWidgetItem(str(val)))
        width, height = self.width(), self.height()
        self.setFixedSize(width, height)
        self.table.resizeColumnsToContents()

    def add_row(self):
        self.table.setRowCount(self.table.rowCount() + 1)

    def data_unsaved(self):
        self.data_saved_to_db = False

    def get_data_from_table(self):
        data = []
        rows = self.table.rowCount()
        cols = self.table.columnCount()
        for row in range(rows):
            tmp = []
            for col in range(cols):
                tmp.append(self.table.item(row, col).text())
            data.append(tmp)
        return data

    def delete_rows(self):
        global con, cur
        rows = list(set([elem.row() for elem in self.table.selectedItems()]))
        ids = [self.table.item(i, 0).text() for i in rows]
        valid = QMessageBox.question(self, "Удаление данных",
                                     "Действительно хотите удалить элементы с id " +
                                     ", ".join(ids), QMessageBox.Yes | QMessageBox.No)
        if valid == QMessageBox.Yes:
            for row in rows:
                self.table.removeRow(row)
            con.commit()

    def save_data_to_db(self):
        global con, cur
        cur.execute("""DELETE FROM snippets""")
        data = self.get_data_from_table()
        for row in data:
            try:
                cur.execute(f"""INSERT INTO snippets (id,input,output) VALUES 
                ({int(row[0])}, '{row[1]}', '{row[2]}')""")
            except ValueError:
                self.parent.error_dialog.showMessage("Неверный тип входных данных!")
        con.commit()
        self.data_saved_to_db = True
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent):
        # Если данные не сохранены, вывести диалог с соответствующим предложением
        if not self.data_saved_to_db:
            save_dialog = QMessageBox.question(self, "Сохранение данных",
                                               "Хотите сохранить изменения перед выходом?",
                                               QMessageBox.Yes |
                                               QMessageBox.No | QMessageBox.Cancel)
            if save_dialog == QMessageBox.Yes:
                self.save_data_to_db()
                event.accept()
            elif save_dialog == QMessageBox.Cancel:
                event.ignore()


class SliderWindow(QDialog, SliderDialog):
    # Класс для окна слайдера для изменения divider_ratio
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setupUi(self)
        self.setWindowIcon(QIcon("IMG/latex_logo.png"))
        self.slider.setMinimum(25)
        self.slider.setMaximum(75)
        self.slider.setValue(int(divider_ratio * 100))
        self.slider.valueChanged.connect(self.slider_value_changed)
        self.value_label.setText(f"{divider_ratio}")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.ok_button.clicked.connect(self.resize_parent)
        self.cancel_button.clicked.connect(self.close)
        width, height = self.width(), self.height()
        self.setFixedSize(width, height)

    def slider_value_changed(self):
        global divider_ratio
        divider_ratio = self.slider.value() / 100
        self.value_label.setText(f"{divider_ratio}")

    def resize_parent(self):
        width, height = self.parent.width(), self.parent.height()
        self.parent.resize(width + 1, height)
        self.parent.resize(width, height)
        self.close()


class AddThemeWindow(QDialog, AddThemeDialog):
    # Класс для добавления темы
    def __init__(self, new_theme_number, parent=None):
        self.parent = parent
        super().__init__(self.parent)
        self.new_theme_number = new_theme_number
        self.setupUi(self)
        self.setWindowIcon((QIcon("IMG/latex_logo.png")))
        self.background_button.clicked.connect(self.select_color)
        self.plain_text_button.clicked.connect(self.select_color)
        self.text_color_button.clicked.connect(self.select_color)
        self.variables_button.clicked.connect(self.select_color)
        self.ok_button.clicked.connect(self.save_theme)
        self.cancel_button.clicked.connect(self.close)
        self.new_style = [self.new_theme_number, None, None, None, None]
        self.default_stylesheet = "border-radius: 10px; height: 50px; border-width: 1px; " \
                                  "border-style: solid; border-color: rgb(180, 180, 180); "
        button_back = "220, 220, 220"
        button_front = "0, 0, 0"
        self.theme_saved = False
        self.background_button.setStyleSheet(self.default_stylesheet +
                                             f"background: rgb({button_back}); " +
                                             f"color: rgb({button_front});")
        self.text_color_button.setStyleSheet(self.default_stylesheet +
                                             f"background: rgb({button_back}); " +
                                             f"color: rgb({button_front});")
        self.plain_text_button.setStyleSheet(self.default_stylesheet +
                                             f"background: rgb({button_back}); " +
                                             f"color: rgb({button_front});")
        self.variables_button.setStyleSheet(self.default_stylesheet +
                                            f"background: rgb({button_back}); " +
                                            f"color: rgb({button_front});")
        width, height = self.width(), self.height()
        self.setFixedSize(width, height)

    def select_color(self):
        # Открыть диалог выбора цвета и изменить цвет кнопок
        color = QColorDialog.getColor().getRgb()[:-1]
        button_back = ", ".join([str(channel) for channel in color])
        button_front = ", ".join([str(255 - channel) for channel in color])
        if self.sender() == self.background_button:
            self.background_button.setStyleSheet(self.default_stylesheet +
                                                 f"background: rgb({button_back}); " +
                                                 f"color: rgb({button_front});")
            self.new_style[1] = button_back
        elif self.sender() == self.text_color_button:
            self.text_color_button.setStyleSheet(self.default_stylesheet +
                                                 f"background: rgb({button_back}); " +
                                                 f"color: rgb({button_front});")
            self.new_style[2] = button_back
        elif self.sender() == self.plain_text_button:
            self.plain_text_button.setStyleSheet(self.default_stylesheet +
                                                 f"background: rgb({button_back}); " +
                                                 f"color: rgb({button_front});")
            self.new_style[3] = button_back
        elif self.sender() == self.variables_button:
            self.variables_button.setStyleSheet(self.default_stylesheet +
                                                f"background: rgb({button_back}); " +
                                                f"color: rgb({button_front});")
            self.new_style[4] = button_back

    def save_theme(self):
        # Сохраняет новую тему в БД и обновляет набор тем в коде
        global con, cur, COLORS
        if not all(self.new_style):
            self.parent.error_dialog.showMessage("Вы не закончили редактирование темы")
            return
        cur.execute(f"""INSERT INTO themes (number,background,text_color,plain_text,variables)
        VALUES ({self.new_style[0]},'{self.new_style[1]}','{self.new_style[2]}',
        '{self.new_style[3]}','{self.new_style[4]}')""")
        con.commit()
        COLORS = get_colors_from_db()
        self.theme_saved = True
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent):
        # Диалог с предложением сохранить тему
        if self.theme_saved:
            event.accept()
        elif all(self.new_style):
            save_theme = QMessageBox.question(self, "Сохранение", "Вы хотите сохранить тему?",
                                              QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if save_theme == QMessageBox.Yes:
                self.save_theme()
            elif save_theme == QMessageBox.No:
                event.accept()
            else:
                event.ignore()


def main():
    app = QApplication(sys.argv)
    latex_editor = LatexEditor()
    latex_editor.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
