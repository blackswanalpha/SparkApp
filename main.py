import sys
import os
import subprocess
import pty
import threading
import select
import sqlite3
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTreeView, QVBoxLayout, QWidget,
    QHBoxLayout, QToolBar, QPushButton, QStatusBar, QTextEdit,
    QSplitter, QDialog, QDialogButtonBox, QLabel, QLineEdit, QFormLayout,
    QMessageBox, QScrollArea, QSplashScreen, QFrame, QComboBox
)
from PyQt6.QtCore import Qt, QSize, QTimer, QRegularExpression, QObject, pyqtSignal
from PyQt6.QtGui import QFont, QFileSystemModel, QPixmap, QSyntaxHighlighter, QTextCharFormat, QColor, QTextCursor


class TerminalOutputReader(QObject):
    """
    A thread-safe reader for terminal output that uses Qt's signal/slot mechanism
    to safely update the GUI from a background thread.
    """
    # Define a signal for new output
    outputReceived = pyqtSignal(str)

    def __init__(self, master_fd):
        """Initialize the output reader with the master file descriptor."""
        super().__init__()
        self.master_fd = master_fd
        self.running = True

    def read_output(self):
        """
        Continuously read output from the terminal and emit it through a signal.
        This method runs in a separate thread.
        """
        while self.running:
            try:
                # Check for available data with a timeout
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if r:
                    # Read available data
                    output = os.read(self.master_fd, 1024).decode()
                    # Emit the output through the signal
                    self.outputReceived.emit(output)
            except (OSError, UnicodeDecodeError) as e:
                # Handle potential errors during reading
                if self.running:  # Only emit error if we're still supposed to be running
                    self.outputReceived.emit(f"\nError reading output: {str(e)}\n")
                break

    def stop(self):
        """Safely stop the output reader."""
        self.running = False


class TerminalEmulator(QWidget):
    """
    A thread-safe terminal emulator widget that provides a Unix-like shell interface.
    Uses Qt's event system to safely handle communication between the GUI and shell process.
    """

    def __init__(self, parent=None):
        """Initialize the terminal emulator with proper thread safety."""
        super().__init__(parent)
        self.setup_terminal()
        self.start_shell()

    def setup_terminal(self):
        """Configure the terminal interface with thread-safe components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create output display area
        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setFont(QFont("Consolas", 11))
        self.terminal_output.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                border: 2px solid #e9ecef;
               
                margin:20px;
                padding: 8px;
            }
        """)

        # Create command input line
        self.command_input = QLineEdit()
        self.command_input.setFont(QFont("Consolas", 11))
        self.command_input.setStyleSheet("""
            QLineEdit {
                background-color: #2D2D2D;
                color: #FFFFFF;
                border: none;
                padding: 8px;
            }
        """)
        self.command_input.returnPressed.connect(self.execute_command)

        # Add prompt label
        self.prompt_label = QLabel("$ ")
        self.prompt_label.setStyleSheet("color: #00FF00;")

        # Create command input layout
        input_layout = QHBoxLayout()
        input_layout.addWidget(self.prompt_label)
        input_layout.addWidget(self.command_input)

        # Assemble the terminal layout
        layout.addWidget(self.terminal_output)
        layout.addLayout(input_layout)

    def start_shell(self):
        """Initialize the shell process with proper thread handling."""
        try:
            # Create pseudo-terminal
            master_fd, slave_fd = pty.openpty()

            # Start shell process
            self.shell_process = subprocess.Popen(
                os.environ.get('SHELL', '/bin/bash'),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid
            )

            # Close slave file descriptor
            os.close(slave_fd)

            # Store master file descriptor
            self.master_fd = master_fd

            # Create and configure output reader
            self.output_reader = TerminalOutputReader(self.master_fd)
            self.output_reader.outputReceived.connect(self.update_terminal_output)

            # Start reader thread
            self.reader_thread = threading.Thread(target=self.output_reader.read_output)
            self.reader_thread.daemon = True
            self.reader_thread.start()

        except Exception as e:
            QMessageBox.critical(self, "Terminal Error", f"Failed to start terminal: {str(e)}")

    def update_terminal_output(self, output):
        """
        Update the terminal output in a thread-safe way.
        This slot is automatically called in the GUI thread.
        """
        try:
            self.terminal_output.insertPlainText(output)
            # Scroll to bottom
            cursor = self.terminal_output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.terminal_output.setTextCursor(cursor)
        except RuntimeError:
            # Widget has been deleted, stop the reader
            self.cleanup()

    def execute_command(self):
        """Execute the command in a thread-safe manner."""
        try:
            command = self.command_input.text()
            if command.strip():
                # Display command in output
                self.terminal_output.append(f"$ {command}")

                # Send command to shell
                os.write(self.master_fd, (command + '\n').encode())

                # Clear input line
                self.command_input.clear()
        except OSError as e:
            self.terminal_output.append(f"\nError executing command: {str(e)}\n")

    def cleanup(self):
        """
        Clean up resources safely.
        This method ensures proper shutdown of threads and processes.
        """
        if hasattr(self, 'output_reader'):
            self.output_reader.stop()

        if hasattr(self, 'shell_process'):
            try:
                self.shell_process.terminate()
                self.shell_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.shell_process.kill()

        if hasattr(self, 'master_fd'):
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    def closeEvent(self, event):
        """Handle the terminal widget's close event safely."""
        self.cleanup()
        super().closeEvent(event)

class CodeSyntaxHighlighter(QSyntaxHighlighter):
    """
    A syntax highlighter for programming code that supports multiple languages.
    Provides color-coding for keywords, strings, comments, and other code elements.
    """

    def __init__(self, parent, language='python'):
        super().__init__(parent)
        self.language = language
        self.highlighting_rules = []
        self.setup_highlighting_rules()

    def setup_highlighting_rules(self):
        """Configure syntax highlighting rules based on the selected language."""
        self.highlighting_rules = []

        # Define color formats for different code elements
        keyword_format = self.create_format("#FF7043")  # Orange for keywords
        class_format = self.create_format("#42A5F5")  # Blue for class names
        string_format = self.create_format("#66BB6A")  # Green for strings
        comment_format = self.create_format("#78909C")  # Gray for comments
        number_format = self.create_format("#AB47BC")  # Purple for numbers
        function_format = self.create_format("#FFA726")  # Orange for functions

        # Language-specific patterns
        if self.language.lower() == 'python':
            # Python keywords
            keywords = [
                'and', 'as', 'assert', 'break', 'class', 'continue', 'def',
                'del', 'elif', 'else', 'except', 'False', 'finally', 'for',
                'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'None',
                'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'True',
                'try', 'while', 'with', 'yield'
            ]

            # Add keyword patterns
            for word in keywords:
                pattern = QRegularExpression(r'\b' + word + r'\b')
                self.highlighting_rules.append((pattern, keyword_format))

            # Python string patterns (single and double quotes)
            self.highlighting_rules.extend([
                (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format),
                (QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format),
                # Triple quotes
                (
                QRegularExpression(r'""".*?"""', QRegularExpression.PatternOption.DotMatchesEverythingOption), string_format),
                (
                QRegularExpression(r"'''.*?'''", QRegularExpression.PatternOption.DotMatchesEverythingOption), string_format),
            ])

            # Python comments
            self.highlighting_rules.append(
                (QRegularExpression(r'#[^\n]*'), comment_format)
            )

            # Python class names
            self.highlighting_rules.append(
                (QRegularExpression(r'\bclass\s+\w+'), class_format)
            )

            # Python function definitions
            self.highlighting_rules.append(
                (QRegularExpression(r'\bdef\s+\w+'), function_format)
            )

        elif self.language.lower() == 'javascript':
            # JavaScript keywords
            keywords = [
                'break', 'case', 'catch', 'class', 'const', 'continue',
                'debugger', 'default', 'delete', 'do', 'else', 'export',
                'extends', 'finally', 'for', 'function', 'if', 'import',
                'in', 'instanceof', 'new', 'return', 'super', 'switch',
                'this', 'throw', 'try', 'typeof', 'var', 'void', 'while',
                'with', 'yield', 'let', 'static', 'enum', 'await', 'async'
            ]

            for word in keywords:
                pattern = QRegularExpression(r'\b' + word + r'\b')
                self.highlighting_rules.append((pattern, keyword_format))

            # JavaScript strings
            self.highlighting_rules.extend([
                (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format),
                (QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format),
                (QRegularExpression(r'`[^`\\]*(\\.[^`\\]*)*`'), string_format),  # Template literals
            ])

            # JavaScript comments
            self.highlighting_rules.extend([
                (QRegularExpression(r'//[^\n]*'), comment_format),
                (
                QRegularExpression(r'/\*.*?\*/', QRegularExpression.PatternOption.DotMatchesEverythingOption), comment_format)
            ])

        # Common patterns for all languages
        # Numbers
        self.highlighting_rules.append(
            (QRegularExpression(r'\b\d+\b'), number_format)
        )

    def create_format(self, color, style=None):
        """Create a text format with specified color and optional style."""
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        if style:
            text_format.setFontWeight(style)
        return text_format

    def highlightBlock(self, text):
        """Apply highlighting rules to the given block of text."""
        for pattern, format in self.highlighting_rules:
            matches = pattern.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)


class CodeEditor(QTextEdit):
    """
    Enhanced text editor with code editing features like syntax highlighting,
    line numbers, and auto-indentation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_editor()
        self.highlighter = None
        self.set_language('python')  # Default to Python

    def setup_editor(self):
        """Configure the editor with code-friendly settings."""
        # Use a monospace font
        font = QFont("Consolas", 12)
        font.setFixedPitch(True)
        self.setFont(font)

        # Set tab width
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

        # Enable line wrapping
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def set_language(self, language):
        """Set the programming language for syntax highlighting."""
        self.highlighter = CodeSyntaxHighlighter(self.document(), language)


class FileEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SPARK")
        self.setGeometry(300, 150, 1000, 600)
        self.setMinimumSize(QSize(1200, 700))

        self.current_file_path = None
        self.conn = sqlite3.connect('quick_actions.db')
        self.create_table()
        self.init_ui()
        # Initialize SQLite connection here
        # self.conn = sqlite3.connect('quick_actions.db')

        # Connect to SQLite3 database


        # self.connect_to_database()

    def init_ui(self):
        self.create_toolbar()
        self.create_status_bar()
        self.create_main_layout()
        # Modern UI stylesheet with a clean, professional design
        self.setStyleSheet("""
                /* Main Window - Creating a clean base */
                QMainWindow {
                    background-color: #f5f6fa;
                }

                /* Toolbar - Elevated design with subtle shadow */
                QToolBar {
                    background-color: #ffffff;
                    border-bottom: 1px solid #e1e4e8;
                    padding: 10px;
                    spacing: 15px;
                }

                /* Status Bar - Minimal design */
                QStatusBar {
                    background-color: #ffffff;
                    border-top: 1px solid #e1e4e8;
                    color: #586069;
                    padding: 5px;
                }

                /* Tree View - Modern file explorer */
                QTreeView {
                    background-color: #ffffff;
                    border: none;
                    border-right: 1px solid #e1e4e8;
                    padding: 8px;
                    margin: 0px;
                    font-size: 14px;
                }
                QTreeView::item {
                    padding: 8px;
                    border-radius: 6px;
                    margin: 2px 0px;
                }
                QTreeView::item:selected {
                    background-color: #f1f8ff;
                    color: #0366d6;
                    font-weight: bold;
                }
                QTreeView::item:hover {
                    background-color: #f6f8fa;
                }

                /* Text Editor - Clean and focused design */
                QTextEdit {
                    background-color: #ffffff;
                    border: none;
                    padding: 15px;
                    selection-background-color: #0366d6;
                    selection-color: white;
                    font-family: 'Consolas', monospace;
                    font-size: 14px;
                    line-height: 1.6;
                }

                /* Buttons - Modern, flat design */
                QPushButton {
                    background-color: #0366d6;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 13px;
                    min-width: 80px;
                }
                QPushButton:hover {
                    background-color: #0255b3;
                }
                QPushButton:pressed {
                    background-color: #024494;
                }
                QPushButton#deleteButton {
                    background-color: #ea4aaa;
                }
                QPushButton#deleteButton:hover {
                    background-color: #d03592;
                }

                /* Search Bar and Line Edits - Modern input fields */
                QLineEdit {
                    padding: 10px;
                    border: 2px solid #e1e4e8;
                    border-radius: 6px;
                    background-color: white;
                    font-size: 13px;
                    min-width: 200px;
                }
                QLineEdit:focus {
                    border-color: #0366d6;
                    background-color: #f8fafd;
                }

                /* Labels - Clear typography */
                QLabel {
                    color: #24292e;
                    font-size: 14px;
                    padding: 2px;
                }
                QLabel#headerLabel {
                    font-size: 20px;
                    font-weight: bold;
                    color: #24292e;
                    padding: 15px 10px;
                }

                /* Quick Action Cards - Elevated card design */
                QWidget[class="card"] {
                    background-color: white;
                    border: 1px solid #e1e4e8;
                    border-radius: 10px;
                    padding: 15px;
                    margin: 10px;
                }
                QWidget[class="card"]:hover {
                    border-color: #0366d6;
                    background-color: #f8fafd;
                }

                /* Scroll Area - Clean scrolling experience */
                QScrollArea {
                    background-color: #f5f6fa;
                    border: none;
                    padding: 5px;
                }
                QScrollBar:vertical {
                    border: none;
                    background: #f5f6fa;
                    width: 10px;
                    margin: 0px;
                }
                QScrollBar::handle:vertical {
                    background-color: #c1c8d1;
                    border-radius: 5px;
                    min-height: 20px;
                }
                QScrollBar::handle:vertical:hover {
                    background-color: #a8b1bd;
                }

                /* Combo Box - Modern dropdown */
                QComboBox {
                    background-color: white;
                    border: 2px solid #e1e4e8;
                    border-radius: 6px;
                    padding: 8px;
                    min-width: 150px;
                    font-size: 13px;
                }
                QComboBox:hover {
                    border-color: #0366d6;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 20px;
                }
                QComboBox::down-arrow {
                    image: none;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-top: 5px solid #586069;
                }

                /* Dialog Windows - Consistent styling */
                QDialog {
                    background-color: white;
                    border-radius: 10px;
                }
                QDialog QLabel {
                    font-size: 14px;
                    color: #24292e;
                }

                /* Splitter - Refined separators */
                QSplitter::handle {
                    background-color: #e1e4e8;
                }
                QSplitter::handle:horizontal {
                    width: 1px;
                }
                QSplitter::handle:vertical {
                    height: 1px;
                }

                /* Terminal styling */
                QWidget#terminal {
                    background-color: #ffffff;
                    border: 1px solid #e1e4e8;
                    border-radius: 6px;
                    margin: 10px;
                    padding: 10px;
                }
            """)

    def connect_to_database(self):
        try:
            self.conn = sqlite3.connect('quick_actions.db')
            self.create_table()
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Database Error", f"Error connecting to database: {e}")

            # ... rest of the FileEditor class ...

    # def __del__(self):
    #     if self.conn:
    #         self.conn.close()
    def create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)

        new_file_btn = QPushButton("+")
        new_file_btn.clicked.connect(self.create_new_item)
        toolbar.addWidget(new_file_btn)

        save_file_btn = QPushButton("Save")
        save_file_btn.clicked.connect(self.save_current_file)
        toolbar.addWidget(save_file_btn)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search Files")
        toolbar.addWidget(self.search_bar)

        create_card_button = QPushButton("Create Quick Action Card")
        create_card_button.clicked.connect(self.create_card_from_toolbar)
        toolbar.addWidget(create_card_button)

    def create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def create_main_layout(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizes([200, 600, 250])  # Example: Set initial sizes for each pane
        self.create_file_system_view(splitter)
        self.create_editor_view(splitter)
        self.create_quick_action_pane(splitter)
        splitter.setSizes([200, 500, 350])  # Example: Set initial sizes for each pane
        layout.addWidget(splitter)

        # Create vertical splitter for editor and terminal
        v_splitter = QSplitter(Qt.Orientation.Vertical)

        # Add terminal
        terminal = TerminalEmulator()
        terminal.setMaximumHeight(250)
        v_splitter.addWidget(terminal)
        # Set initial vertical sizes (editor larger than terminal)
        v_splitter.setSizes([200])
        layout.addWidget(v_splitter)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def create_file_system_view(self, parent_splitter):
        home_directory = os.path.expanduser("~")
        file_system_model = QFileSystemModel()
        file_system_model.setRootPath(home_directory)

        self.tree_view = QTreeView()
        self.tree_view.setModel(file_system_model)
        self.tree_view.setRootIndex(file_system_model.index(home_directory))
        self.tree_view.clicked.connect(self.on_tree_view_clicked)
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setMinimumWidth(180)

        # Hide columns except for the first (file/folder name)
        for i in range(1, file_system_model.columnCount()):
            self.tree_view.setColumnHidden(i, True)

        parent_splitter.addWidget(self.tree_view)

    def create_editor_view(self, parent_splitter):
        self.editor = CodeEditor()
        self.editor.setFont(QFont("System", 12))
        self.editor.setPlaceholderText("Select a file to start editing...")
        self.editor.setDisabled(True)

        editor_widget = QWidget()
        layout = QVBoxLayout(editor_widget)

        layout.addWidget(self.editor)

        # Add header with language selector
        # header_layout = QHBoxLayout()
        # header = QLabel("Editor")
        # header.setObjectName("headerLabel")
        # header_layout.addWidget(header)
        #
        # # Add language selector
        # self.language_selector = QComboBox()
        # self.language_selector.addItems(['Plain Text', 'Python', 'JavaScript'])
        # self.language_selector.currentTextChanged.connect(self.change_language)
        # header_layout.addWidget(self.language_selector)
        # header_layout.addStretch()
        # layout.addLayout(header_layout)

        parent_splitter.addWidget(editor_widget)

    def change_language(self, language):
        """Change the syntax highlighting language."""
        if language == 'Plain Text':
            self.editor.highlighter = None
            self.editor.document().setPlainText(self.editor.toPlainText())
        else:
            self.editor.set_language(language.lower())

    def open_file(self, path):
        """Open and display a file with appropriate syntax highlighting."""
        try:
            self.current_file_path = path
            with open(path, 'r') as file:
                content = file.read()

            # Detect language from file extension
            ext = os.path.splitext(path)[1].lower()
            language_map = {
                '.py': 'Python',
                '.js': 'JavaScript',
            }

            # Set language in selector and apply highlighting
            detected_language = language_map.get(ext, 'Plain Text')
            self.language_selector.setCurrentText(detected_language)

            # Set content
            self.editor.setPlainText(content)
            self.editor.setDisabled(False)
            self.status_bar.showMessage(f"Opened: {path}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open file: {e}")

    def create_quick_action_pane(self, parent_splitter):
        quick_action_pane = QWidget()
        quick_action_layout = QVBoxLayout()
        quick_action_pane.setLayout(quick_action_layout)
        quick_action_pane.setMaximumWidth(300)

        title = QLabel("Quick Actions")
        title.setFont(QFont("System", 14))
        quick_action_layout.addWidget(title)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)  # Remove scroll area border
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # Hide horizontal scrollbar
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("background-color: #f8f9fa; padding:0px;")
        self.scroll_area_widget = QWidget()
        self.scroll_area_layout = QVBoxLayout()
        self.scroll_area_widget.setLayout(self.scroll_area_layout)
        self.scroll_area.setWidget(self.scroll_area_widget)
        quick_action_layout.addWidget(self.scroll_area)

        # self.create_card_form(quick_action_layout)

        parent_splitter.addWidget(quick_action_pane)

        self.load_cards_from_db()

    def create_card_form(self, layout):
        form_layout = QFormLayout()

        self.card_title_input = QLineEdit()
        self.card_title_input.setPlaceholderText("Card Title")
        form_layout.addRow("Title", self.card_title_input)

        self.card_content_input = QTextEdit()
        self.card_content_input.setPlaceholderText("Card Content")
        form_layout.addRow("Content", self.card_content_input)

        add_card_button = QPushButton("Add Card")
        add_card_button.clicked.connect(self.create_card)
        form_layout.addRow(add_card_button)

        layout.addLayout(form_layout)

    def on_tree_view_clicked(self, index):
        path = self.tree_view.model().filePath(index)
        if not self.tree_view.model().isDir(index):
            self.open_file(path)

    def open_file(self, path):
        try:
            self.current_file_path = path
            with open(path, 'r') as file:
                self.editor.setText(file.read())
            self.editor.setDisabled(False)
            self.status_bar.showMessage(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open file: {e}")

    def create_new_item(self):
        dialog = NewItemDialog(self)
        if dialog.exec():
            item_type, name = dialog.get_inputs()
            index = self.tree_view.currentIndex()
            path = self.tree_view.model().filePath(index)
            self.create_item(item_type, name, path)

    def create_item(self, item_type, name, path):
        try:
            full_path = os.path.join(path, name)
            if item_type.lower() == "file":
                open(full_path, 'w').close()
            else:
                os.makedirs(full_path, exist_ok=True)
            self.refresh_file_system_view()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create {item_type.lower()}: {e}")

    def refresh_file_system_view(self):
        home_directory = os.path.expanduser("~")
        self.tree_view.model().setRootPath('')
        self.tree_view.setRootIndex(self.tree_view.model().index(home_directory))

    def save_current_file(self):
        if self.current_file_path:
            try:
                with open(self.current_file_path, 'w') as file:
                    file.write(self.editor.toPlainText())
                self.status_bar.showMessage(f"Saved: {self.current_file_path}")
                QMessageBox.information(self, "File Saved", f"File saved successfully: {self.current_file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save file: {e}")
        else:
            QMessageBox.warning(self, "No File Selected", "No file selected to save.")

    def create_card(self):
        title = self.card_title_input.text().strip()
        content = self.card_content_input.toPlainText().strip()

        if not title or not content:
            QMessageBox.warning(self, "Input Error", "Both title and content are required.")
            return

        card_widget = self.create_card_widget(title, content)
        self.scroll_area_layout.addWidget(card_widget)

        self.save_card_to_db(title, content)

        self.card_title_input.clear()
        self.card_content_input.clear()

    def create_card_widget(self, title, content):
        card_widget = QWidget()
        card_widget.setStyleSheet("""
                                  QWidget {
                background-color: white;
                border-radius: 8px;
                padding: 12px;
                margin: 8px;
                
            }
            QWidget:hover {
            border: 2px solid #e9ecef;
                border-color: #0d6efd;
            }
        """)
        layout = QVBoxLayout()
        layout = QVBoxLayout()

        card_title = QLabel(title)
        card_title.setFont(QFont("System", 12))
        layout.addWidget(card_title)

        card_content = QLabel(content)
        layout.addWidget(card_content)

        actions_layout = QHBoxLayout()
        edit_button = QPushButton("Edit")
        edit_button.setStyleSheet("background-color: green; color: white; border-radius: 3px;")
        edit_button.clicked.connect(lambda: self.edit_card(card_widget, title, content))
        actions_layout.addWidget(edit_button)

        delete_button = QPushButton("Delete")
        delete_button.setStyleSheet("background-color: red; color: white; border-radius: 3px;")
        delete_button.clicked.connect(lambda: self.delete_card(card_widget, title))
        actions_layout.addWidget(delete_button)

        layout.addLayout(actions_layout)

        card_widget.setLayout(layout)
        return card_widget

    def edit_card(self, card_widget, old_title, old_content):
        title, content = self.get_card_edit_input(old_title, old_content)
        if title and content:
            card_widget.findChild(QLabel).setText(title)
            card_widget.findChildren(QLabel)[1].setText(content)
            self.update_card_in_db(old_title, title, content)

    def delete_card(self, card_widget, title):
        self.scroll_area_layout.removeWidget(card_widget)
        card_widget.deleteLater()
        self.delete_card_from_db(title)

    def get_card_edit_input(self, old_title, old_content):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Quick Action Card")

        form_layout = QFormLayout()
        title_input = QLineEdit(old_title)
        content_input = QTextEdit(old_content)
        form_layout.addRow("Title", title_input)
        form_layout.addRow("Content", content_input)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        form_layout.addRow(button_box)

        dialog.setLayout(form_layout)
        if dialog.exec():
            return title_input.text().strip(), content_input.toPlainText().strip()
        return None, None

    def save_card_to_db(self, title, content):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO quick_actions (title, content) VALUES (?, ?)", (title, content))
        self.conn.commit()

    def update_card_in_db(self, old_title, new_title, new_content):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE quick_actions SET title = ?, content = ? WHERE title = ?", (new_title, new_content, old_title))
        self.conn.commit()

    def delete_card_from_db(self, title):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM quick_actions WHERE title = ?", (title,))
        self.conn.commit()

    def load_cards_from_db(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT title, content FROM quick_actions")
        for title, content in cursor.fetchall():
            card_widget = self.create_card_widget(title, content)
            self.scroll_area_layout.addWidget(card_widget)

    def create_table(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quick_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def create_card_from_toolbar(self):
        title, content = self.get_card_edit_input("", "")
        if title and content:
            card_widget = self.create_card_widget(title, content)
            self.scroll_area_layout.addWidget(card_widget)
            self.save_card_to_db(title, content)


class NewItemDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New File/Folder")
        self.setModal(True)

        self.item_type = QLineEdit()
        self.name = QLineEdit()

        form_layout = QFormLayout()
        form_layout.addRow("Item Type (File/Folder)", self.item_type)
        form_layout.addRow("Name", self.name)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        form_layout.addRow(button_box)

        self.setLayout(form_layout)

    def get_inputs(self):
        return self.item_type.text().strip(), self.name.text().strip()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Splash screen
    splash_pix = QPixmap("splash.png")  # Ensure this image path is correct
    splash = QSplashScreen(splash_pix, Qt.WindowType.SplashScreen)
    splash.setMask(splash_pix.mask())
    splash.show()

    main_win = FileEditor()

    # Simulating a long startup task
    QTimer.singleShot(3000, main_win.show)  # Show the main window after 3 seconds
    QTimer.singleShot(3000, splash.close)  # Close the splash screen after 3 seconds

    sys.exit(app.exec())
