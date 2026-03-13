import sys
import os
import re
import sqlite3
import psycopg2
import mysql.connector
import ollama
import google.generativeai as genai
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
import json

# --- SAP Metadata Dictionary ---
SAP_METADATA = {
    "Material": {
        "tables": {
            "MARA": {"description": "General Material Data", "fields": ["MATNR", "MTART", "MATKL"]},
            "MARC": {"description": "Plant Data for Material", "fields": ["WERKS", "EKGRP", "BESKZ"]},
            "MAKT": {"description": "Material Description", "fields": ["MAKTX", "SPRAS"]},
            "MVKE": {"description": "Sales Data", "fields": ["VKORG", "VTWEG"]}
        },
        "joins": [
            "MARA.MATNR = MARC.MATNR",
            "MARA.MATNR = MAKT.MATNR",
            "MARA.MATNR = MVKE.MATNR"
        ],
        "all_fields": [
            {"table": "MARA", "fields": ["MATNR", "MTART", "MATKL"]},
            {"table": "MARC", "fields": ["WERKS", "EKGRP", "BESKZ"]},
            {"table": "MAKT", "fields": ["MAKTX", "SPRAS"]},
            {"table": "MVKE", "fields": ["VKORG", "VTWEG"]}
        ],
        "base_table": "MARA"
    }
}

# --- UI Constants (Premium Light Theme) ---
PRIMARY_BLUE = "#2563EB"    # Sharp Professional Blue
SECONDARY_BLUE = "#3B82F6"  # Lighter Accent Blue
BG_LIGHT = "#FFFFFF"        # Pure White
PANEL_BG = "#F8FAFC"        # Very Soft Gray
CARD_BG = "#FFFFFF"         # Card Background
TEXT_MAIN = "#0F172A"       # Deep Slate for text
TEXT_SUBTLE = "#64748B"     # Slate for secondary text
ACCENT_GREEN = "#10B981"
ACCENT_RED = "#EF4444"
BORDER_COLOR = "#E2E8F0"    # Light border

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
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {PRIMARY_BLUE}, stop:1 {SECONDARY_BLUE});
                    border-radius: 18px;
                    border-bottom-right-radius: 4px;
                }}
                QLabel {{ 
                    color: white; 
                    font-size: 14px; 
                    background: transparent; 
                    line-height: 1.5;
                    font-weight: 500;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: #F1F5F9;
                    border: 1px solid {BORDER_COLOR};
                    border-radius: 18px;
                    border-bottom-left-radius: 4px;
                }}
                QLabel {{ 
                    color: {TEXT_MAIN}; 
                    font-size: 14px; 
                    background: transparent; 
                    line-height: 1.5;
                }}
            """)
        
        layout.addWidget(self.label)
        self.setFixedWidth(420)
        self.setSizePolicy(QTableWidget.sizePolicy(self).horizontalPolicy(), QTableWidget.sizePolicy(self).verticalPolicy().Preferred)

        # Soft shadow for bubbles
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 15))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

class AILogicThread(QThread):
    response_ready = pyqtSignal(str, str)
    error_signal = pyqtSignal(str)

    def __init__(self, history, current_objective, ai_provider, db_context="", conn_params=None):
        super().__init__()
        self.history = history
        self.current_objective = current_objective
        self.ai_provider = ai_provider
        self.db_context = db_context
        self.conn_params = conn_params

    def run(self):
        try:
            task_context = f"CURRENT TASK OBJECTIVE: {self.current_objective}\n" if self.current_objective else ""
            db_info = f"\nCONNECTED DATABASE CONTEXT:\n{self.db_context}\n" if self.db_context else ""
            system_prompt = (
                "You are the SAP SQL EXPERT. You MUST follow this EXACT logic for query generation.\n\n"
                "SAP METADATA DICTIONARY:\n"
                f"{json.dumps(SAP_METADATA, indent=2)}\n\n"
                "STRICT 10-STEP SOP:\n"
                "1. DOMAIN: Identify the domain (e.g., Material).\n"
                "2. ALL FIELDS: You MUST return EVERY field listed in 'all_fields' for that domain in the SELECT list.\n"
                "3. MINIMAL TABLES: Identify ONLY the tables required for requested data or validation.\n"
                "4. NA RULE: If a table is NOT used in the JOIN, replace its fields in the SELECT with 'NA' AS FIELD_NAME.\n"
                "5. SCHEMA: Prefix EVERY table with 'prd_tme.' (unless specified otherwise in context).\n"
                "6. JOINS: Use ONLY the joins specified in the metadata dictionary. NEVER invent joins.\n"
                "7. VALIDATION: If the user asks for a check/flag, add EXACTLY ONE column: CASE WHEN [rule] THEN 1 ELSE 0 END AS ERROR_CONDITION.\n"
                "8. STRUCTURE: Construct SELECT, FROM, JOIN structure clearly.\n"
                "9. VALIDATE: Double-check that all 'all_fields' are present and 'NA' is used correctly.\n"
                "10. OUTPUT: Return ONLY the SQL code block. No explanations, no text before or after.\n\n"
                "TECHNICAL CONSTRAINTS:\n"
                "1. Use ONLY SQL code blocks (```sql ... ```).\n"
                "2. NO extra text. Just SQL.\n"
                f"{task_context}"
                f"{db_info}"
            )
            
            for attempt in range(3): # AI has 3 attempts to get it right
                if self.ai_provider == "gemini":
                    genai.configure(api_key=config.GEMINI_API_KEY)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    
                    # Convert history to Gemini format
                    gemini_history = []
                    for msg in self.history:
                        role = "user" if msg["role"] == "user" else "model"
                        gemini_history.append({"role": role, "parts": [msg["content"]]})
                    
                    chat = model.start_chat(history=gemini_history)
                    input_text = system_prompt + "\n\nUser request: " + self.history[-1]["content"] if attempt == 0 else self.history[-1]["content"]
                    response = chat.send_message(input_text)
                    content = response.text
                else:
                    messages = [{"role": "system", "content": system_prompt}]
                    messages.extend(self.history)
                    response = ollama.chat(model=config.LLAMA_MODEL_NAME, messages=messages)
                    content = response['message']['content']
                
                # Extract SQL for verification
                sql_match = re.search(r'```sql\s*(.*?)\s*```', content, re.DOTALL | re.IGNORECASE)
                if sql_match and self.conn_params:
                    sql = sql_match.group(1).strip()
                    error = self._verify_sql(sql)
                    if error:
                        # Feed the error back to the AI context for next iteration
                        self.history.append({"role": "assistant", "content": content})
                        self.history.append({"role": "user", "content": f"Verification Error: The SQL you provided failed with the following error:\n{error}\nPlease analyze this error, fix the query, and provide the corrected version within a new SQL block."})
                        continue # Retry
                break # Success or no SQL to verify
                
            new_task = self.current_objective
            task_match = re.search(r'TASK_OBJECTIVE:\s*(.*)', content, re.IGNORECASE)
            if task_match:
                new_task = task_match.group(1).strip()
                content = content.replace(task_match.group(0), "").strip()
            self.response_ready.emit(content, new_task)
        except Exception as e:
            self.error_signal.emit(str(e))

    def _verify_sql(self, sql):
        """Helper to test SQL against the database (always rolls back)"""
        try:
            if not self.conn_params: return None
            
            db_type = self.conn_params['type']
            h = self.conn_params['host']
            u = self.conn_params['user']
            p = self.conn_params['pass']
            d = self.conn_params['name']
            
            if db_type == "SQLite":
                conn = sqlite3.connect(d)
            elif db_type == "PostgreSQL":
                if ":" in h:
                    host, port = h.split(":")
                    conn = psycopg2.connect(host=host, port=port, user=u, password=p, dbname=d)
                else:
                    conn = psycopg2.connect(host=h, user=u, password=p, dbname=d)
            elif db_type == "MySQL":
                port = 3306
                if ":" in h: h, port = h.split(":")
                conn = mysql.connector.connect(host=h, port=port, user=u, password=p, database=d)
            
            cursor = conn.cursor()
            cursor.execute(sql)
            # Important: Always rollback to keep verification side-effect free
            conn.rollback()
            conn.close()
            return None # Success
        except Exception as e:
            return str(e)

class SQLAssistantApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SQL Studio")
        self.setMinimumSize(1400, 950)
        self.conversation_history = []
        self.current_objective = None
        self.db_context_info = ""
        
        self._apply_global_styles()
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # Sidebar (Minimalist Icons)
        self.sidebar = self.create_sidebar()
        self.main_layout.addWidget(self.sidebar)
        
        # Content Area
        self.content_area = QWidget()
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(24, 24, 24, 24)
        self.content_layout.setSpacing(24)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        
        self.panel_1 = self.create_chat_panel()
        self.panel_2 = self.create_editor_panel()
        self.panel_3 = self.create_viewer_panel()
        
        self.splitter.addWidget(self.panel_1)
        self.splitter.addWidget(self.panel_2)
        self.splitter.addWidget(self.panel_3)
        self.splitter.setSizes([450, 550, 400])
        
        self.content_layout.addWidget(self.splitter)
        self.main_layout.addWidget(self.content_area)
        
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet(f"color: {TEXT_SUBTLE}; background: {BG_LIGHT}; border-top: 1px solid {BORDER_COLOR}; padding: 5px;")
        self.status_bar.showMessage("SQL Intelligence Online")

    def _apply_global_styles(self):
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG_LIGHT}; }}
            QWidget {{ font-family: 'Inter', sans-serif; color: {TEXT_MAIN}; }}
            
            QLabel#panel_header {{ font-weight: 800; font-size: 18px; color: {TEXT_MAIN}; letter-spacing: -0.5px; }}
            
            QLineEdit {{
                background-color: {BG_LIGHT};
                border: 1px solid {BORDER_COLOR};
                border-radius: 12px;
                padding: 12px 16px;
                color: {TEXT_MAIN};
                font-size: 14px;
            }}
            QLineEdit:focus {{ border: 1px solid {PRIMARY_BLUE}; }}
            
            QPushButton {{
                background-color: {PRIMARY_BLUE};
                color: white;
                border-radius: 10px;
                font-weight: 700;
                padding: 10px 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {SECONDARY_BLUE}; }}
            
            QComboBox {{
                background-color: white;
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
                padding: 6px 10px;
                color: {TEXT_MAIN};
            }}
            
            QTableWidget {{
                background-color: white;
                border: 1px solid {BORDER_COLOR};
                border-radius: 12px;
                gridline-color: #F1F5F9;
                color: {TEXT_MAIN};
            }}
            QHeaderView::section {{
                background-color: #F8FAFC;
                color: {TEXT_SUBTLE};
                padding: 12px;
                border: none;
                border-bottom: 1px solid {BORDER_COLOR};
                font-weight: 700;
                text-transform: uppercase;
                font-size: 11px;
            }}
            
            /* Custom Scrollbar */
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: 5px;
            }}
            QScrollBar::handle:vertical {{
                background: #CBD5E1;
                border-radius: 2px;
            }}
        """)

    def create_sidebar(self):
        bar = QFrame()
        bar.setFixedWidth(64)
        bar.setStyleSheet(f"background-color: #F8FAFC; border-right: 1px solid {BORDER_COLOR};")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, 40, 0, 40)
        layout.setSpacing(32)
        
        for icon_text in ["◈", "⊞", "⚛", "▦"]:
            btn = QLabel(icon_text)
            btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            btn.setStyleSheet(f"font-size: 22px; color: #94A3B8;")
            if icon_text == "◈": btn.setStyleSheet(f"font-size: 22px; color: {PRIMARY_BLUE};")
            layout.addWidget(btn)
        
        layout.addStretch()
        return bar

    def create_chat_panel(self):
        panel = QFrame()
        panel.setStyleSheet(f"background-color: {PANEL_BG}; border-radius: 24px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        header_layout = QHBoxLayout()
        header = QLabel("Neural Chat")
        header.setObjectName("panel_header")
        
        self.new_task_btn = QPushButton("Reset Task")
        self.new_task_btn.setStyleSheet(f"""
            QPushButton {{ 
                background-color: white; 
                border: 1px solid {BORDER_COLOR}; 
                padding: 8px 16px; 
                border-radius: 8px; 
                font-size: 12px;
                color: {TEXT_SUBTLE};
            }}
            QPushButton:hover {{ background-color: {ACCENT_RED}; color: white; border-color: {ACCENT_RED}; }}
        """)
        self.new_task_btn.clicked.connect(self.handle_new_task)
        
        self.model_selector = QComboBox()
        self.model_selector.addItems(["Gemini", "Llama"])
        self.model_selector.setStyleSheet(f"""
            QComboBox {{
                background-color: white;
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
                padding: 4px 8px;
                font-size: 11px;
                color: {TEXT_SUBTLE};
                min-width: 80px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {TEXT_SUBTLE}; margin-right: 8px; }}
        """)
        # Set default based on config
        if config.AI_PROVIDER == "gemini":
            self.model_selector.setCurrentText("Gemini")
        else:
            self.model_selector.setCurrentText("Llama")

        header_layout.addWidget(header)
        header_layout.addWidget(self.model_selector)
        header_layout.addStretch()
        header_layout.addWidget(self.new_task_btn)
        layout.addLayout(header_layout)
        
        self.objective_display = QFrame()
        self.objective_display.setStyleSheet(f"""
            QFrame {{
                background-color: #EFF6FF;
                border: 1px solid #DBEAFE;
                border-radius: 16px;
                padding: 16px;
            }}
        """)
        obj_layout = QVBoxLayout(self.objective_display)
        self.obj_label = QLabel("Current Objective: Waiting for input...")
        self.obj_label.setWordWrap(True)
        self.obj_label.setStyleSheet(f"color: {PRIMARY_BLUE}; font-weight: 700; font-size: 13px;")
        obj_layout.addWidget(self.obj_label)
        layout.addWidget(self.objective_display)
        
        self.chat_history_area = QScrollArea()
        self.chat_history_area.setWidgetResizable(True)
        self.chat_history_area.setStyleSheet("background: transparent; border: none;")
        self.chat_content = QWidget()
        self.chat_content_layout = QVBoxLayout(self.chat_content)
        self.chat_content_layout.addStretch()
        self.chat_history_area.setWidget(self.chat_content)
        layout.addWidget(self.chat_history_area)
        
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Describe your SQL requirements...")
        self.chat_input.setFixedHeight(54)
        self.chat_input.setStyleSheet(f"background-color: white; border-radius: 14px; padding: 0 20px; border: 1px solid {BORDER_COLOR};")
        self.chat_input.returnPressed.connect(self.handle_send)
        layout.addWidget(self.chat_input)
        
        return panel

    def create_editor_panel(self):
        panel = QFrame()
        panel.setStyleSheet(f"background-color: {PANEL_BG}; border-radius: 24px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        header = QLabel("Studio")
        header.setObjectName("panel_header")
        layout.addWidget(header)
        
        self.query_editor = QTextBrowser()
        self.query_editor.setStyleSheet(f"background-color: #0F172A; border-radius: 16px; border: none;")
        layout.addWidget(self.query_editor)
        
        self.editor_status = QLabel("")
        self.editor_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.editor_status.setStyleSheet("font-size: 11px; font-weight: 700;")
        layout.addWidget(self.editor_status)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setStyleSheet(f"background-color: white; border: 1px solid {BORDER_COLOR}; color: {TEXT_SUBTLE};")
        
        self.copy_btn = QPushButton("Copy SQL")
        self.copy_btn.setStyleSheet(f"background-color: {PRIMARY_BLUE};")
        
        self.commit_btn = QPushButton("Commit")
        self.commit_btn.setStyleSheet(f"background-color: {ACCENT_GREEN};")
        
        btn_layout.addWidget(self.revert_btn)
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.commit_btn)
        layout.addLayout(btn_layout)
        
        self.commit_btn.clicked.connect(self.handle_commit)
        self.revert_btn.clicked.connect(self.handle_revert)
        self.copy_btn.clicked.connect(self.handle_copy)
        
        return panel

    def create_viewer_panel(self):
        panel = QFrame()
        panel.setStyleSheet(f"background-color: {PANEL_BG}; border-radius: 24px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        header = QLabel("Data")
        header.setObjectName("panel_header")
        layout.addWidget(header)
        
        conn_card = QFrame()
        conn_card.setStyleSheet(f"background-color: white; border-radius: 16px; border: 1px solid {BORDER_COLOR};")
        clayout = QVBoxLayout(conn_card)
        clayout.setSpacing(10)
        
        self.db_type = QComboBox()
        self.db_type.addItems(["SQLite", "PostgreSQL", "MySQL"])
        clayout.addWidget(self.db_type)
        
        # Connection Inputs
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("Host (e.g. localhost)")
        clayout.addWidget(self.host_input)
        
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("Username")
        clayout.addWidget(self.user_input)
        
        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("Password")
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        clayout.addWidget(self.pass_input)
        
        self.db_name_input = QLineEdit()
        self.db_name_input.setPlaceholderText("Database Name / Path")
        clayout.addWidget(self.db_name_input)
        
        # Set defaults from config for user ease
        db_type_map = {"sqlite": "SQLite", "postgres": "PostgreSQL", "mysql": "MySQL"}
        default_idx = self.db_type.findText(db_type_map.get(config.DB_TYPE.lower(), "SQLite"))
        if default_idx >= 0:
            self.db_type.setCurrentIndex(default_idx)

        self.host_input.setText(config.DB_CONFIG.get("host", "localhost"))
        self.user_input.setText(config.DB_CONFIG.get("user", "root"))
        self.pass_input.setText(config.DB_CONFIG.get("password", ""))
        self.db_name_input.setText(config.DB_CONFIG.get("database", "test.db"))
        
        self.connect_btn = QPushButton("Sync Connection")
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #DBEAFE;
                color: {PRIMARY_BLUE};
                border: none;
                padding: 10px;
                border-radius: 8px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {PRIMARY_BLUE}; color: white; }}
        """)
        self.connect_btn.clicked.connect(self.handle_connect)
        clayout.addWidget(self.connect_btn)
        
        self.conn_status_label = QLabel("")
        self.conn_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.conn_status_label.setStyleSheet("font-size: 11px; font-weight: 700; margin-top: 4px;")
        clayout.addWidget(self.conn_status_label)
        
        layout.addWidget(conn_card)
        
        self.table_list = QComboBox()
        self.table_list.currentTextChanged.connect(self.on_table_selected)
        layout.addWidget(self.table_list)
        
        self.results_table = QTableWidget()
        layout.addWidget(self.results_table)
        
        return panel

    def add_message(self, text, is_user=True):
        container = QWidget()
        clayout = QHBoxLayout(container)
        clayout.setContentsMargins(0, 0, 0, 0)
        bubble = ChatBubble(text, is_user)
        if is_user:
            clayout.addStretch()
            clayout.addWidget(bubble)
        else:
            clayout.addWidget(bubble)
            clayout.addStretch()
        
        self.chat_content_layout.takeAt(self.chat_content_layout.count() - 1)
        self.chat_content_layout.addWidget(container)
        self.chat_content_layout.addStretch()
        
        if text != "Thinking...":
            self.conversation_history.append({"role": "user" if is_user else "assistant", "content": text})
            
        QApplication.processEvents()
        self.chat_history_area.verticalScrollBar().setValue(self.chat_history_area.verticalScrollBar().maximum())
        return container

    def handle_send(self):
        text = self.chat_input.text().strip()
        if not text: return
        self.add_message(text, is_user=True)
        self.chat_input.clear()
        self.thinking_message = self.add_message("Thinking...", is_user=False)
        provider = self.model_selector.currentText().lower()
        
        # Pass connection params for verification loop
        conn_params = None
        if hasattr(self, 'conn') and self.conn:
            conn_params = {
                'type': self.db_type.currentText(),
                'host': self.host_input.text(),
                'user': self.user_input.text(),
                'pass': self.pass_input.text(),
                'name': self.db_name_input.text()
            }
            
        self.ai_thread = AILogicThread(self.conversation_history, self.current_objective, provider, self.db_context_info, conn_params)
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
        
        # Robust SQL Extraction - capture multiple blocks if present
        sql_blocks = re.findall(r'```sql\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        if sql_blocks:
            # Combine or take the last one as the primary "requirement"
            full_sql = "\n\n".join([b.strip() for b in sql_blocks])
            self.update_query_editor(full_sql)

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
        while self.chat_content_layout.count() > 1:
            item = self.chat_content_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.status_bar.showMessage("Neural Sync Complete. Context reset.", 3000)

    def update_query_editor(self, sql):
        formatter = HtmlFormatter(nowrap=True, style='monokai')
        highlighted = highlight(sql, SqlLexer(), formatter)
        css = formatter.get_style_defs('.highlight')
        # Professional dark editor for the code section even in light theme
        html = f"<html><head><style>{css} .highlight {{ background: transparent; color: #f8f8f2; }} pre {{ font-family: 'JetBrains Mono', monospace; font-size: 14px; margin: 0; padding: 10px; }}</style></head><body><div class='highlight'><pre>{highlighted}</pre></div></body></html>"
        self.query_editor.setHtml(html)

    def handle_connect(self):
        try:
            db_type = self.db_type.currentText()
            h, u, p, d = self.host_input.text(), self.user_input.text(), self.pass_input.text(), self.db_name_input.text()
            
                
            if db_type == "SQLite":
                self.conn = sqlite3.connect(d)
            elif db_type == "PostgreSQL":
                # Handle port if it looks like host:port
                if ":" in h:
                    host, port = h.split(":")
                    self.conn = psycopg2.connect(host=host, port=port, user=u, password=p, dbname=d)
                else:
                    self.conn = psycopg2.connect(host=h, user=u, password=p, dbname=d)
            elif db_type == "MySQL":
                # Handle port if provided in config or host string
                port = 3306
                if ":" in h:
                    h, port = h.split(":")
                elif "port" in config.DB_CONFIG:
                    port = config.DB_CONFIG["port"]
                    
                self.conn = mysql.connector.connect(
                    host=h, 
                    port=port,
                    user=u, 
                    password=p, 
                    database=d
                )
                
            if self.conn.is_connected():
                self.conn_status_label.setText("connected")
                self.conn_status_label.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 11px; font-weight: 700;")
                self.status_bar.showMessage("Neural Sync Success | Learning Database...", 5000)
                self.refresh_tables()
                self.fetch_database_context()
            else:
                raise Exception("Failed to establish connection")
        except Exception as e:
            self.conn_status_label.setText("not connected")
            self.conn_status_label.setStyleSheet(f"color: {ACCENT_RED}; font-size: 11px; font-weight: 700;")
            self.status_bar.showMessage(f"Sync Fail: {e}")

    def refresh_tables(self):
        cursor = self.conn.cursor()
        db_type = self.db_type.currentText()
        if db_type == "SQLite":
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        elif db_type == "PostgreSQL":
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
        elif db_type == "MySQL":
            cursor.execute("SHOW TABLES;")

        tables = [r[0] for r in cursor.fetchall()]
        self.table_list.clear()
        self.table_list.addItems(tables)
        return tables

    def fetch_database_context(self):
        try:
            cursor = self.conn.cursor()
            tables = self.refresh_tables()
            context = "Detailed Database Schema:\n"
            
            for table in tables[:15]: # Limit to first 15 tables for context efficiency
                context += f"- Table: {table}\n"
                
                db_type = self.db_type.currentText()
                if db_type == "SQLite":
                    cursor.execute(f"PRAGMA table_info({table});")
                    cols = [f"{r[1]} ({r[2]})" for r in cursor.fetchall()]
                elif db_type == "PostgreSQL":
                    cursor.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}';")
                    cols = [f"{r[0]} ({r[1]})" for r in cursor.fetchall()]
                elif db_type == "MySQL":
                    cursor.execute(f"DESCRIBE {table};")
                    cols = [f"{r[0]} ({r[1]})" for r in cursor.fetchall()]
                
                context += f"  Columns: {', '.join(cols)}\n"
                
                # Sample data
                try:
                    cursor.execute(f"SELECT * FROM {table} LIMIT 2;")
                    sample = cursor.fetchall()
                    if sample:
                        context += f"  Sample Data: {str(sample)}\n"
                except:
                    pass
            
            self.db_context_info = context
            self.status_bar.showMessage("Neural Sync Success | AI Mind Synced with DB", 3000)
        except Exception as e:
            print(f"Failed to fetch context: {e}")
            self.db_context_info = "Schema retrieval failed, using fallback knowledge."

    def on_table_selected(self, name):
        if name: self.execute_query(f"SELECT * FROM {name} LIMIT 100;")

    def handle_commit(self):
        if not hasattr(self, 'conn') or self.conn is None:
            self.editor_status.setText("NOT CONNECTED")
            self.editor_status.setStyleSheet(f"color: {ACCENT_RED}; font-size: 11px; font-weight: 700;")
            return
            
        sql = self.query_editor.toPlainText()
        if sql: self.execute_query(sql)

    def execute_query(self, sql):
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            
            self.editor_status.setText("EXECUTION SUCCESS")
            self.editor_status.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 11px; font-weight: 700;")
            
            if any(m in sql.upper() for m in ["INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE"]):
                self.conn.commit()
                self.refresh_tables()
                return
            rows = cursor.fetchall()[:100]
            cols = [d[0] for d in cursor.description]
            
            # Update results table in the third panel
            self.results_table.setColumnCount(len(cols))
            self.results_table.setRowCount(len(rows))
            self.results_table.setHorizontalHeaderLabels(cols)
            self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for i, r in enumerate(rows):
                for j, v in enumerate(r):
                    self.results_table.setItem(i, j, QTableWidgetItem(str(v)))
        except Exception as e:
            self.editor_status.setText("EXECUTION FAILED")
            self.editor_status.setStyleSheet(f"color: {ACCENT_RED}; font-size: 11px; font-weight: 700;")
            self.status_bar.showMessage(f"Execution Error: {e}")

    def handle_copy(self):
        QApplication.clipboard().setText(self.query_editor.toPlainText())
        self.status_bar.showMessage("Query clip-synced", 2000)

    def handle_revert(self):
        self.query_editor.clear()
        self.add_message("Objective reverted", is_user=False)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = SQLAssistantApp()
    window.show()
    sys.exit(app.exec())
