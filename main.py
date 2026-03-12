import sys
import os
import re
import sqlite3
import psycopg2
import ollama
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QTextBrowser, QLineEdit, QPushButton, QScrollArea,
    QLabel, QFrame, QGridLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QStatusBar, QHeaderView, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QIcon, QTextCursor, QColor, QPalette, QLinearGradient, QBrush
import config
from pygments import highlight
from pygments.lexers import SqlLexer
from pygments.formatters import HtmlFormatter

# --- UI Constants (Streva Style) ---
PRIMARY_PURPLE = "#8B5CF6"
SECONDARY_PURPLE = "#6366F1"
BG_DARK = "#0F172A"
CARD_BG = "#1E293B"
TEXT_WHITE = "#F8FAFC"
ACCENT_GREEN = "#10B981"
ACCENT_RED = "#EF4444"

class ChatBubble(QFrame):
    def __init__(self, text, is_user=True):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(0)
        
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        if is_user:
            self.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {PRIMARY_PURPLE}, stop:1 {SECONDARY_PURPLE});
                    border-radius: 20px;
                    border-bottom-right-radius: 4px;
                }}
                QLabel {{ 
                    color: white; 
                    font-size: 14px; 
                    background: transparent; 
                    line-height: 1.4;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: {CARD_BG};
                    border: 1px solid #334155;
                    border-radius: 20px;
                    border-bottom-left-radius: 4px;
                }}
                QLabel {{ 
                    color: {TEXT_WHITE}; 
                    font-size: 14px; 
                    background: transparent; 
                    line-height: 1.4;
                }}
            """)
        
        layout.addWidget(self.label)
        
        # Sizing Policy
        self.setFixedWidth(400) # Keep width fixed but allow height to expand naturally
        self.setSizePolicy(QTableWidget.sizePolicy(self).horizontalPolicy(), QTableWidget.sizePolicy(self).verticalPolicy().Preferred)

        # Subtle shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

class AILogicThread(QThread):
    response_ready = pyqtSignal(str, str) # text, identified_task
    error_signal = pyqtSignal(str)

    def __init__(self, history, current_task):
        super().__init__()
        self.history = history
        self.current_task = current_task

    def run(self):
        try:
            task_context = f"CURRENT TASK OBJECTIVE: {self.current_task}\n" if self.current_task else ""
            system_prompt = (
                "You are the Streva SQL Intelligence.\n"
                f"{task_context}"
                "INSTRUCTIONS:\n"
                "1. If context is missing, ask: 'Do you want a generic SQL template, or should we build this for your real-time database? If it's for your database, please provide the Domain, Table, and Fields.'\n"
                "2. 'All columns' or '*' is a valid input for Fields.\n"
                "3. ONCE YOU HAVE Domain, Table, and Fields (even if they were provided in the first message), PROCEED IMMEDIATELY to generate the SQL in a ```sql block.\n"
                "4. DO NOT repeat the clarification question once the information has been provided.\n"
                "5. Use the TASK_OBJECTIVE tag at the end.\n"
                "\n"
                "EXAMPLE:\n"
                "User: 'Select all columns from users in domain prod'\n"
                "AI: 'Understood. Generating the query for your real-time database.'\n"
                "```sql\nSELECT * FROM users;\n```\n"
                "TASK_OBJECTIVE: Selecting all columns from the users table."
            )
            
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self.history)
            
            response = ollama.chat(
                model=config.LLAMA_MODEL_NAME,
                messages=messages
            )
            
            content = response['message']['content']
            
            # Parse identified task
            new_task = self.current_task
            task_match = re.search(r'TASK_OBJECTIVE:\s*(.*)', content, re.IGNORECASE)
            if task_match:
                new_task = task_match.group(1).strip()
                content = content.replace(task_match.group(0), "").strip()
                
            self.response_ready.emit(content, new_task)
        except Exception as e:
            self.error_signal.emit(str(e))

class SQLAssistantApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Streva SQL Assistant")
        self.setMinimumSize(1300, 900)
        
        # State
        self.conversation_history = []
        self.current_objective = None
        
        # Global Stylesheet
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG_DARK}; }}
            QWidget {{ font-family: 'Inter', -apple-system, sans-serif; }}
            QLabel#panel_header {{ color: white; font-weight: 800; font-size: 18px; }}
            
            QLineEdit {{
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 25px;
                padding: 12px 20px;
                color: white;
                font-size: 14px;
            }}
            QLineEdit:focus {{ border: 1px solid {PRIMARY_PURPLE}; }}
            
            QPushButton {{
                background-color: {PRIMARY_PURPLE};
                color: white;
                border-radius: 12px;
                font-weight: bold;
                padding: 10px 20px;
            }}
            QPushButton:hover {{ background-color: {SECONDARY_PURPLE}; }}
            
            QTextBrowser {{
                background-color: {BG_DARK};
                border: 1px solid #334155;
                border-radius: 15px;
                padding: 10px;
            }}
            
            QTableWidget {{
                background-color: {CARD_BG};
                color: white;
                gridline-color: #334155;
                border: 1px solid #334155;
                border-radius: 12px;
            }}
            QHeaderView::section {{
                background-color: #0F172A;
                color: #94A3B8;
                padding: 10px;
                border: none;
                font-weight: bold;
            }}
        """)
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(20)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(2)
        
        self.panel_1 = self.create_chat_panel()
        self.panel_2 = self.create_editor_panel()
        self.panel_3 = self.create_viewer_panel()
        
        self.splitter.addWidget(self.panel_1)
        self.splitter.addWidget(self.panel_2)
        self.splitter.addWidget(self.panel_3)
        self.splitter.setSizes([450, 500, 350])
        
        self.main_layout.addWidget(self.splitter)
        
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("color: #64748B; background: transparent; border: none; font-size: 12px;")
        self.status_bar.showMessage("Ready to design queries...")

    def create_chat_panel(self):
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        
        header_layout = QHBoxLayout()
        header = QLabel("AI Intelligence")
        header.setObjectName("panel_header")
        
        self.new_task_btn = QPushButton("Complete Task")
        self.new_task_btn.setStyleSheet(f"""
            QPushButton {{ 
                background-color: {PRIMARY_PURPLE}; 
                border: none; 
                padding: 8px 16px; 
                border-radius: 12px; 
                font-size: 13px;
                color: white;
            }}
            QPushButton:hover {{ background-color: {ACCENT_RED}; }}
        """)
        self.new_task_btn.clicked.connect(self.handle_new_task)
        
        header_layout.addWidget(header)
        header_layout.addStretch()
        header_layout.addWidget(self.new_task_btn)
        layout.addLayout(header_layout)
        
        # Context Window (Objective Display)
        self.objective_display = QFrame()
        self.objective_display.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(139, 92, 246, 0.08);
                border: 1px dashed {PRIMARY_PURPLE};
                border-radius: 16px;
                padding: 12px;
            }}
        """)
        obj_layout = QVBoxLayout(self.objective_display)
        self.obj_label = QLabel("Current Objective: Waiting for input...")
        self.obj_label.setStyleSheet(f"color: white; font-weight: 600; font-size: 13px;")
        
        info_sub = QLabel("AI will focus on this goal until you complete the task.")
        info_sub.setStyleSheet("color: #94A3B8; font-size: 11px;")
        
        obj_layout.addWidget(self.obj_label)
        obj_layout.addWidget(info_sub)
        layout.addWidget(self.objective_display)
        
        self.chat_history_area = QScrollArea()
        self.chat_history_area.setWidgetResizable(True)
        self.chat_history_area.setStyleSheet("background: transparent; border: none;")
        self.chat_content = QWidget()
        self.chat_content.setStyleSheet("background: transparent;")
        self.chat_content_layout = QVBoxLayout(self.chat_content)
        self.chat_content_layout.setSpacing(15)
        self.chat_content_layout.addStretch()
        self.chat_history_area.setWidget(self.chat_content)
        layout.addWidget(self.chat_history_area)
        
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Describe your SQL task...")
        self.chat_input.returnPressed.connect(self.handle_send)
        layout.addWidget(self.chat_input)
        
        return panel

    def create_editor_panel(self):
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        
        header = QLabel("Query Studio")
        header.setObjectName("panel_header")
        layout.addWidget(header)
        
        self.query_editor = QTextBrowser()
        self.query_editor.setPlaceholderText("Synthesized SQL will appear here...")
        layout.addWidget(self.query_editor)
        
        # Better Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        self.commit_btn = QPushButton("Commit to Database")
        self.commit_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_GREEN};
                padding: 16px;
                font-size: 14px;
                border-radius: 16px;
                font-weight: 800;
            }}
            QPushButton:hover {{ background-color: #059669; margin-top: -2px; }}
        """)
        
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #1E293B;
                border: 1px solid #334155;
                padding: 16px;
                border-radius: 16px;
                color: #94A3B8;
            }}
            QPushButton:hover {{ background-color: {ACCENT_RED}; color: white; }}
        """)
        
        self.copy_btn = QPushButton("Copy SQL")
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY_PURPLE};
                padding: 16px;
                border-radius: 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {SECONDARY_PURPLE}; }}
        """)
        
        btn_layout.addWidget(self.commit_btn, 2)
        btn_layout.addWidget(self.revert_btn, 1)
        btn_layout.addWidget(self.copy_btn, 1)
        layout.addLayout(btn_layout)
        
        self.commit_btn.clicked.connect(self.handle_commit)
        self.revert_btn.clicked.connect(self.handle_revert)
        self.copy_btn.clicked.connect(self.handle_copy)
        
        return panel

    def create_viewer_panel(self):
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        
        header = QLabel("Data Source")
        header.setObjectName("panel_header")
        layout.addWidget(header)
        
        # Connection Card
        conn_card = QFrame()
        conn_card.setStyleSheet(f"background-color: {CARD_BG}; border-radius: 20px; border: 1px solid #334155;")
        c_layout = QVBoxLayout(conn_card)
        c_layout.setSpacing(8)
        
        self.db_type = QComboBox()
        self.db_type.addItems(["SQLite", "PostgreSQL"])
        c_layout.addWidget(self.db_type)
        
        self.conn_input = QLineEdit("test.db")
        c_layout.addWidget(self.conn_input)
        
        self.connect_btn = QPushButton("Sync Connection")
        self.connect_btn.clicked.connect(self.handle_connect)
        c_layout.addWidget(self.connect_btn)
        
        layout.addWidget(conn_card)
        
        layout.addWidget(QLabel("Tables:"))
        self.table_list = QComboBox()
        self.table_list.currentTextChanged.connect(self.on_table_selected)
        layout.addWidget(self.table_list)
        
        self.results_table = QTableWidget()
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        layout.addWidget(self.results_table)
        
        return panel

    def add_message(self, text, is_user=True):
        bubble_container = QWidget()
        bubble_layout = QHBoxLayout(bubble_container)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        
        bubble = ChatBubble(text, is_user)
        if is_user:
            bubble_layout.addStretch()
            bubble_layout.addWidget(bubble)
        else:
            bubble_layout.addWidget(bubble)
            bubble_layout.addStretch()
            
        self.chat_content_layout.takeAt(self.chat_content_layout.count() - 1)
        self.chat_content_layout.addWidget(bubble_container)
        self.chat_content_layout.addStretch()
        
        if text != "Thinking...":
            self.conversation_history.append({"role": "user" if is_user else "assistant", "content": text})
            
        QApplication.processEvents()
        self.chat_history_area.verticalScrollBar().setValue(self.chat_history_area.verticalScrollBar().maximum())
        return bubble_container

    def handle_send(self):
        text = self.chat_input.text().strip()
        if not text: return
        
        self.add_message(text, is_user=True)
        self.chat_input.clear()
        
        self.thinking_message = self.add_message("Thinking...", is_user=False)
        self.ai_thread = AILogicThread(self.conversation_history, self.current_objective)
        self.ai_thread.response_ready.connect(self.on_ai_response)
        self.ai_thread.error_signal.connect(self.on_ai_error)
        self.ai_thread.start()

    def on_ai_response(self, text, new_task):
        self.chat_content_layout.removeWidget(self.thinking_message)
        self.thinking_message.deleteLater()
        
        if new_task and (not self.current_objective or len(new_task) > 5):
            self.current_objective = new_task
            self.obj_label.setText(f"Current Objective: {self.current_objective}")
            
        self.add_message(text, is_user=False)
        
        # Robust SQL Extraction including fallback
        sql_match = re.search(r'```sql\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        if sql_match:
            self.update_query_editor(sql_match.group(1).strip())
        elif any(kw in text.upper() for kw in ["SELECT ", "UPDATE ", "INSERT "]):
            # Try to grab the first block of text that looks like a query
            lines = text.split('\n')
            query_lines = [l for l in lines if any(kw in l.upper() for kw in ["SELECT", "FROM", "WHERE", "JOIN", "UPDATE", "SET"])]
            if query_lines:
                self.update_query_editor("\n".join(query_lines).strip())

    def on_ai_error(self, error):
        if hasattr(self, 'thinking_message'):
            self.chat_content_layout.removeWidget(self.thinking_message)
            self.thinking_message.deleteLater()
        self.add_message(f"Neural Error: {error}", is_user=False)

    def handle_new_task(self):
        self.conversation_history = []
        self.current_objective = None
        self.obj_label.setText("Current Objective: Waiting for input...")
        self.query_editor.clear()
        
        # Clear chat layout
        while self.chat_content_layout.count() > 1:
            item = self.chat_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.status_bar.showMessage("Neural Sync Complete. Context reset.", 3000)

    def update_query_editor(self, sql):
        formatter = HtmlFormatter(nowrap=True, style='monokai')
        highlighted = highlight(sql, SqlLexer(), formatter)
        css = formatter.get_style_defs('.highlight')
        
        html = f"""
        <html><head><style>
            {css}
            .highlight {{ background: transparent; color: #f8f8f2; }}
            pre {{ font-family: 'JetBrains Mono', monospace; font-size: 15px; line-height: 1.6; margin: 0; }}
        </style></head>
        <body><div class='highlight'><pre>{highlighted}</pre></div></body></html>
        """
        self.query_editor.setHtml(html)

    def handle_connect(self):
        try:
            if self.db_type.currentText() == "SQLite":
                self.conn = sqlite3.connect(self.conn_input.text())
            else:
                self.conn = psycopg2.connect(self.conn_input.text()) # simplified for now
                
            self.status_bar.showMessage("Neural Sync Success", 2000)
            self.refresh_tables()
        except Exception as e:
            self.status_bar.showMessage(f"Sync Fail: {e}")

    def refresh_tables(self):
        cursor = self.conn.cursor()
        if self.db_type.currentText() == "SQLite":
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        else:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
        
        tables = [r[0] for r in cursor.fetchall()]
        self.table_list.clear()
        self.table_list.addItems(tables)

    def on_table_selected(self, name):
        if name: self.execute_query(f"SELECT * FROM {name} LIMIT 100;")

    def handle_commit(self):
        sql = self.query_editor.toPlainText()
        if sql and hasattr(self, 'conn'): self.execute_query(sql)

    def execute_query(self, sql):
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            if any(m in sql.upper() for m in ["INSERT", "UPDATE", "DELETE"]):
                self.conn.commit()
                self.refresh_tables()
                return
            
            rows = cursor.fetchall()[:100]
            cols = [d[0] for d in cursor.description]
            self.results_table.setColumnCount(len(cols))
            self.results_table.setRowCount(len(rows))
            self.results_table.setHorizontalHeaderLabels(cols)
            for i, r in enumerate(rows):
                for j, v in enumerate(r):
                    self.results_table.setItem(i, j, QTableWidgetItem(str(v)))
        except Exception as e:
            self.status_bar.showMessage(f"Execution Error: {e}")

    def handle_copy(self):
        QApplication.clipboard().setText(self.query_editor.toPlainText())
        self.status_bar.showMessage("Query clip-synced", 2000)

    def handle_revert(self):
        self.query_editor.clear()
        self.add_message("Objective reverted", is_user=False)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion") # Better base for custom CSS
    window = SQLAssistantApp()
    window.show()
    sys.exit(app.exec())
